"""Historian: Kafka -> MongoDB.

Consumes every telemetry topic in consumer group "historian" and writes batches
into database "scada":

    plc_tags      time-series  {ts, meta: {plc, tag, unit}, value, quality}   one doc per tag change
    imu           time-series  {ts, meta: {device_id}, boot_id, seq, accel, gyro}
    camera_poses  time-series  {ts, meta: {device_id, marker_id}, boot_id, seq, visible, tvec, rvec}
    events        regular      {ts, device_id, type, severity, message, ...}
    devices       regular      {_id: device_id, kind, first_seen, last_seen, tags?}

At-least-once: offsets are committed only after the batch is stored, so a crash
replays the uncommitted messages. Replays inside one historian process are
dropped using the last (boot_id, seq) per device.
"""
import json
import os
import signal
import time
from datetime import datetime

from confluent_kafka import Consumer
from pymongo import MongoClient, UpdateOne
from pymongo.errors import CollectionInvalid, PyMongoError

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
GROUP_ID = os.getenv("KAFKA_GROUP_ID", "historian")
TOPICS = os.getenv("TOPICS", "plc-tags,imu-readings,camera-poses,scada-events").split(",")
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB = os.getenv("MONGO_DB", "scada")
RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "7"))
BATCH = int(os.getenv("BATCH_MAX", "1000"))
BATCH_WAIT = float(os.getenv("BATCH_WAIT_S", "0.5"))

TIMESERIES = {
    "plc_tags": "meta",
    "imu": "meta",
    "camera_poses": "meta",
}

running = True


def _request_stop(signum, frame):
    global running
    running = False


signal.signal(signal.SIGTERM, _request_stop)
signal.signal(signal.SIGINT, _request_stop)


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class Transformer:
    """Turns envelopes into Mongo documents. Pure (no I/O) so it can be tested."""

    def __init__(self):
        self.last_seq = {}   # (device_id, topic) -> (boot_id, seq)
        self.units = {}      # plc id -> {tag: unit}

    def is_duplicate(self, topic, msg):
        key = (msg["device_id"], topic)
        prev = self.last_seq.get(key)
        if prev and prev[0] == msg["boot_id"] and msg["seq"] <= prev[1]:
            return True
        self.last_seq[key] = (msg["boot_id"], msg["seq"])
        return False

    def transform(self, msg):
        """Return ({collection: [docs]}, device_update or None)."""
        out = {}
        ts = parse_ts(msg["ts"])
        data = msg["data"]
        device = msg["device_id"]
        kind = msg["kind"]
        device_update = {"_id": device, "kind": kind, "last_seen": ts}

        if kind == "plc":
            if "schema" in data:
                self.units[device] = {tag: s.get("unit", "") for tag, s in data["schema"].items()}
                device_update["tags"] = data["schema"]
            units = self.units.get(device, {})
            out["plc_tags"] = [
                {
                    "ts": parse_ts(v["ts"]) if v.get("ts") else ts,
                    "meta": {"plc": device, "tag": tag, "unit": units.get(tag, "")},
                    "value": v["v"],
                    "quality": v["q"],
                }
                for tag, v in data.get("tags", {}).items()
            ]
        elif kind == "imu":
            out["imu"] = [{"ts": ts, "meta": {"device_id": device}, "boot_id": msg["boot_id"], "seq": msg["seq"],
                           "accel": data["accel_ms2"], "gyro": data["gyro_dps"]}]
        elif kind == "camera":
            out["camera_poses"] = [
                {"ts": ts, "meta": {"device_id": device, "marker_id": m["marker_id"]},
                 "boot_id": msg["boot_id"], "seq": msg["seq"],
                 "visible": m["visible"], "tvec": m["tvec"], "rvec": m["rvec"]}
                for m in data["markers"]
            ]
        elif kind == "event":
            out["events"] = [{"ts": ts, "device_id": device, **data}]
            device_update = None  # events can name devices that publish nothing themselves
        return out, device_update


def ensure_schema(db):
    existing = set(db.list_collection_names())
    for name, meta in TIMESERIES.items():
        if name not in existing:
            try:
                db.create_collection(
                    name,
                    timeseries={"timeField": "ts", "metaField": meta, "granularity": "seconds"},
                    expireAfterSeconds=RETENTION_DAYS * 86400,
                )
            except CollectionInvalid:
                pass  # created concurrently
    # Secondary indexes for "one device / one tag over time" queries (dashboard history, latest value).
    db.plc_tags.create_index([("meta.plc", 1), ("meta.tag", 1), ("ts", -1)])
    db.imu.create_index([("meta.device_id", 1), ("ts", -1)])
    db.camera_poses.create_index([("meta.device_id", 1), ("ts", -1)])
    db.events.create_index("ts", expireAfterSeconds=RETENTION_DAYS * 86400)  # TTL; also serves ts sorts
    db.events.create_index([("device_id", 1), ("ts", -1)])


def write(db, docs, devices):
    for coll, items in docs.items():
        if items:
            db[coll].insert_many(items, ordered=False)
    if devices:
        db.devices.bulk_write([
            UpdateOne(
                {"_id": d["_id"]},
                {"$set": {k: v for k, v in d.items() if k != "_id"}, "$setOnInsert": {"first_seen": d["last_seen"]}},
                upsert=True,
            )
            for d in devices.values()
        ], ordered=False)


def main():
    db = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000, tz_aware=True)[DB]
    while True:
        try:
            ensure_schema(db)
            break
        except PyMongoError as e:
            print(f"[historian] waiting for MongoDB: {str(e)[:200]}", flush=True)
            time.sleep(3)

    consumer = Consumer({
        "bootstrap.servers": BOOTSTRAP,
        "group.id": GROUP_ID,
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,   # commit only after Mongo has the data
    })
    consumer.subscribe(TOPICS)
    print(f"[historian] {TOPICS} -> {DB} (group {GROUP_ID}, retention {RETENTION_DAYS} d)", flush=True)

    tf = Transformer()
    # Units arrive only with the schema sent when the gateway (re)connects; after a
    # historian restart, recover them from the device registry instead.
    for d in db.devices.find({"kind": "plc", "tags": {"$exists": True}}, {"tags": 1}):
        tf.units[d["_id"]] = {tag: s.get("unit", "") for tag, s in d["tags"].items()}
    stored, last_log = 0, time.monotonic()
    try:
        while running:
            msgs = consumer.consume(num_messages=BATCH, timeout=BATCH_WAIT)
            if not msgs:
                continue
            docs, devices = {}, {}
            for m in msgs:
                if m.error():
                    print(f"[historian] consumer error: {m.error()}", flush=True)
                    continue
                try:
                    env = json.loads(m.value())
                    if tf.is_duplicate(m.topic(), env):
                        continue
                    out, dev = tf.transform(env)
                except (ValueError, KeyError, TypeError) as e:
                    print(f"[historian] skipping bad message on {m.topic()}: {e}", flush=True)
                    continue
                for coll, items in out.items():
                    docs.setdefault(coll, []).extend(items)
                if dev:
                    devices[dev["_id"]] = {**devices.get(dev["_id"], {}), **dev}

            backoff = 1
            while True:  # never commit past data that is not stored
                try:
                    write(db, docs, devices)
                    break
                except PyMongoError as e:
                    print(f"[historian] write failed, retrying in {backoff}s: {str(e)[:200]}", flush=True)
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 30)
            consumer.commit(asynchronous=False)

            stored += sum(len(v) for v in docs.values())
            if time.monotonic() - last_log > 30:
                print(f"[historian] stored {stored} docs in the last {time.monotonic() - last_log:.0f}s", flush=True)
                stored, last_log = 0, time.monotonic()
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
