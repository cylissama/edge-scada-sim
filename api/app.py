"""SCADA API (FastAPI).

GET  /api/stream                     Server-Sent Events: plc, imu, camera, event, topology, kafka
GET  /api/topology                   every Deployment/StatefulSet with status ok|stale|down|stopped
GET  /api/kafka                      per-topic rates, consumer lag
GET  /api/plc                        current tag values + schema per PLC
GET  /api/events?limit=&device=      alarm / event log (historian)
GET  /api/history/{device}?minutes=&tag=&limit=
POST /api/components/{name}/stop     scale a controllable Deployment to 0 (start -> 1)
The built React app (Phase 5) is served from /.
"""
import asyncio
import itertools
import json
import logging
import math
import os
import threading
import time
import urllib.error
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from kube import Kube
from live import KafkaStats, Live, sse
from pymongo import MongoClient
from telemetry import envelope, make_producer, send

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
EVENTS_TOPIC = "scada-events"
STALE_S = float(os.getenv("STALE_S", "3"))
REFRESH_S = float(os.getenv("REFRESH_S", "2"))
STATIC_DIR = Path(__file__).parent / "static"
log = logging.getLogger("uvicorn.error")

state = {"topology": [], "kafka": {}}
db = MongoClient(MONGO_URL, tz_aware=True, serverSelectionTimeoutMS=3000)["scada"]
kube = None       # set in lifespan when running in a cluster
live = None
producer = None
_seq = itertools.count()  # event envelope seq for this API process


def component_status(c, age, plc_bad):
    """ok | stale | down | stopped. Devices must also be producing data to be ok."""
    if c["desired"] == 0:
        return "stopped"
    if c["ready"] < c["desired"]:
        return "down"
    if c["role"] in ("device", "edge-service") and (age is None or age > STALE_S or plc_bad):
        return "stale"
    return "ok"


def build_topology(deployments, statefulsets, pods, last_seen, plc_bad, now):
    components = []
    for obj in deployments + statefulsets:
        meta, spec = obj["metadata"], obj["spec"]
        tpl_labels = spec["template"]["metadata"].get("labels", {})
        app = tpl_labels.get("app", meta["name"])
        running = [p for p in pods if p["metadata"].get("labels", {}).get("app") == app
                   and p["status"].get("phase") == "Running"]
        seen = last_seen.get(meta["name"])
        c = {
            "id": meta["name"],
            "kind": tpl_labels.get("kind", "service"),
            "role": tpl_labels.get("role", "core-service"),
            "tier": spec["template"]["spec"].get("nodeSelector", {}).get("tier", "core"),
            "controllable": meta.get("labels", {}).get("scada/controllable") == "true",
            "desired": spec.get("replicas", 1),
            "ready": obj.get("status", {}).get("readyReplicas", 0),
            "restarts": sum(cs.get("restartCount", 0) for p in running
                            for cs in p["status"].get("containerStatuses", [])),
            "node": running[0]["spec"].get("nodeName") if running else None,
            "last_msg_age": round(now - seen, 1) if seen else None,
        }
        c["status"] = component_status(c, c["last_msg_age"], plc_bad.get(c["id"], False))
        components.append(c)
    return sorted(components, key=lambda c: (c["tier"] != "edge", c["kind"], c["id"]))


def seed_plc_from_history():
    """After an API restart, tags that haven't changed since won't arrive live: read them from Mongo."""
    for dev in db.devices.find({"kind": "plc", "tags": {"$exists": True}}):
        values = {}
        for tag in dev["tags"]:
            doc = db.plc_tags.find_one({"meta.plc": dev["_id"], "meta.tag": tag}, sort=[("ts", -1)])
            if doc:
                values[tag] = {"v": doc["value"], "q": doc["quality"], "ts": doc["ts"].isoformat()}
        live.seed_plc(dev["_id"], dev["tags"], values)


async def refresher():
    kstats = KafkaStats()
    while True:
        if kube:
            try:
                objs = await asyncio.to_thread(kube.snapshot)
                state["topology"] = build_topology(*objs, live.last_seen, live.plc_bad, time.time())
                live.broadcast("topology", json.dumps(state["topology"]))
            except Exception as e:  # keep serving the last snapshot
                log.warning("topology refresh failed: %s", e)
        try:
            state["kafka"] = await asyncio.to_thread(kstats.snapshot)
            live.broadcast("kafka", json.dumps(state["kafka"]))
        except Exception as e:
            log.warning("kafka stats failed: %s", e)
        await asyncio.sleep(REFRESH_S)


@asynccontextmanager
async def lifespan(app):
    global kube, live, producer
    try:
        kube = Kube()
    except (KeyError, OSError):
        log.warning("not running in a cluster: topology and control disabled")
    live = Live(asyncio.get_running_loop())
    producer = make_producer(BOOTSTRAP, "scada-api")
    threading.Thread(target=live.consume_forever, daemon=True, name="kafka-live").start()
    threading.Thread(target=seed_plc_from_history, daemon=True, name="seed-plc").start()
    task = asyncio.create_task(refresher())
    yield
    task.cancel()
    producer.flush(5)


app = FastAPI(title="edge-scada-sim API", lifespan=lifespan)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/topology")
def topology():
    return state["topology"]


@app.get("/api/kafka")
def kafka():
    return state["kafka"]


@app.get("/api/plc")
def plc():
    return live.plc_snapshot()


@app.get("/api/events")
def events(limit: int = Query(100, ge=1, le=1000), device: str | None = None):
    query = {"device_id": device} if device else {}
    return list(db.events.find(query, {"_id": 0}).sort("ts", -1).limit(limit))


@app.get("/api/history/{device}")
def history(device: str, minutes: float = Query(10, gt=0, le=1440), tag: str | None = None,
            limit: int = Query(600, ge=10, le=5000)):
    since = datetime.now(UTC) - timedelta(minutes=minutes)
    dev = db.devices.find_one({"_id": device}, {"kind": 1})
    if not dev:
        raise HTTPException(404, f"unknown device {device}")
    if dev["kind"] == "plc":
        if not tag:
            raise HTTPException(400, "PLC history needs ?tag=")
        cursor = db.plc_tags.find({"meta.plc": device, "meta.tag": tag, "ts": {"$gte": since}},
                                  {"_id": 0, "ts": 1, "value": 1, "quality": 1})
    elif dev["kind"] == "imu":
        cursor = db.imu.find({"meta.device_id": device, "ts": {"$gte": since}},
                             {"_id": 0, "ts": 1, "accel": 1, "gyro": 1})
    elif dev["kind"] == "camera":
        cursor = db.camera_poses.find({"meta.device_id": device, "visible": True, "ts": {"$gte": since}},
                                      {"_id": 0, "ts": 1, "meta.marker_id": 1, "tvec": 1})
    else:
        raise HTTPException(400, f"no history for kind {dev['kind']}")
    docs = list(cursor.sort("ts", 1))
    stride = max(1, math.ceil(len(docs) / limit))  # ponytail: stride downsampling, $bucketAuto if it gets slow
    return docs[::stride]


@app.post("/api/components/{name}/{action}")
def control(name: str, action: Literal["start", "stop"]):
    if not kube:
        raise HTTPException(503, "not running in a cluster")
    try:
        dep = kube.deployment(name)
    except urllib.error.HTTPError as e:
        raise HTTPException(404 if e.code == 404 else 502, f"{name}: {e.reason}") from e
    if dep["metadata"].get("labels", {}).get("scada/controllable") != "true":
        raise HTTPException(403, f"{name} is not controllable from the dashboard")
    replicas = 1 if action == "start" else 0
    kube.scale(name, replicas)
    send(producer, EVENTS_TOPIC, name, envelope(name, "event", next(_seq), {
        "type": f"operator_{action}", "severity": "info", "source": "dashboard",
        "message": f"{name} {'started' if replicas else 'stopped'} from the dashboard",
    }))
    return {"id": name, "desired": replicas}


@app.get("/api/stream")
async def stream(request: Request):
    q = live.subscribe()

    async def events_gen():
        try:
            yield sse("topology", json.dumps(state["topology"]))
            yield sse("kafka", json.dumps(state["kafka"]))
            while not await request.is_disconnected():
                try:
                    yield await asyncio.wait_for(q.get(), 15)
                except TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            live.unsubscribe(q)

    return StreamingResponse(events_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="web")
