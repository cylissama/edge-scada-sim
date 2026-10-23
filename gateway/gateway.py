"""Edge gateway: OPC UA subscriptions on each PLC -> Kafka.

For each PLC it browses the address space (so new tags in the JSON config
appear without changing the gateway), subscribes to every tag, and every
PUBLISH_MS sends the changed values as one message to the plc-tags topic:

    data = {"tags": {"Drive.Speed_Actual": {"v": 1449.2, "q": "Good", "ts": "..."}, ...}}

The first message after each (re)connect also carries "schema" (type, unit,
enum, alarm limits per tag). Alarm transitions, PLC online/offline go to
scada-events. On disconnect the last values are republished with quality
BadNoCommunication, then the gateway reconnects with backoff.
"""
import asyncio
import json
import logging
import os
import signal

from asyncua import Client, ua
from telemetry import envelope, make_producer, send

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
TAGS_TOPIC = os.getenv("TAGS_TOPIC", "plc-tags")
EVENTS_TOPIC = os.getenv("EVENTS_TOPIC", "scada-events")
# "plc-1=opc.tcp://plc-1:4840,plc-2=opc.tcp://plc-2:4840"
PLC_ENDPOINTS = dict(
    item.split("=", 1) for item in os.getenv("PLC_ENDPOINTS", "plc-1=opc.tcp://localhost:4840").split(",")
)
PUBLISH_MS = int(os.getenv("PUBLISH_MS", "250"))
NAMESPACE = "urn:edge-scada-sim"

SEVERITY = {"hihi": "high", "lolo": "high", "hi": "medium", "lo": "medium", "ne": "medium"}


def alarm_level(limits, value):
    """Return the active alarm level for a value ("hihi", "hi", "lo", "lolo", "ne") or None."""
    if "ne" in limits and value != limits["ne"]:
        return "ne"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    for level, breached in (("hihi", lambda x: value >= x), ("lolo", lambda x: value <= x),
                            ("hi", lambda x: value >= x), ("lo", lambda x: value <= x)):
        if level in limits and breached(limits[level]):
            return level
    return None


class Seq:
    """Per-(device, topic) sequence counters for the message envelope."""

    def __init__(self):
        self.n = {}

    def next(self, key):
        self.n[key] = self.n.get(key, -1) + 1
        return self.n[key]


class PlcLink:
    def __init__(self, plc_id, url, producer, seq):
        self.plc_id, self.url, self.producer, self.seq = plc_id, url, producer, seq
        self.paths = {}       # NodeId -> tag path
        self.schema = {}      # tag path -> {type, unit, enum, alarm}
        self.last = {}        # tag path -> last value dict
        self.pending = {}     # tag path -> value dict, not yet published
        self.alarms = {}      # tag path -> active alarm level
        self.online = None

    # asyncua calls this from its subscription task for every value change.
    def datachange_notification(self, node, val, data):
        path = self.paths.get(node.nodeid)
        if path is None:
            return
        if self.schema[path]["type"] == "Float":
            val = float(f"{val:.6g}")  # Float is 32-bit: drop the binary noise (4.510000228881836)
        dv = data.monitored_item.Value
        ts = dv.SourceTimestamp or dv.ServerTimestamp
        ts = ts.isoformat(timespec="milliseconds").replace("+00:00", "Z") if ts else None
        self.pending[path] = {"v": val, "q": dv.StatusCode.name, "ts": ts}
        self.check_alarm(path, val)

    def status_change_notification(self, status):
        print(f"[gateway] {self.plc_id} subscription status: {status.Status.name}", flush=True)

    def event(self, type_, severity, message, **extra):
        data = {"type": type_, "severity": severity, "message": message, **extra}
        key = (self.plc_id, EVENTS_TOPIC)
        send(self.producer, EVENTS_TOPIC, self.plc_id, envelope(self.plc_id, "event", self.seq.next(key), data))
        print(f"[gateway] event {self.plc_id}: {type_} {message}", flush=True)

    def check_alarm(self, path, value):
        limits = self.schema.get(path, {}).get("alarm") or {}
        level = alarm_level(limits, value)
        previous = self.alarms.get(path)
        if level == previous:
            return
        self.alarms[path] = level
        enum = self.schema[path].get("enum") or {}
        shown = enum.get(str(value), value)
        if level:
            self.event("alarm", SEVERITY[level], f"{path} {level.upper()} ({shown})",
                       tag=path, level=level, value=value)
        else:
            self.event("alarm_cleared", "info", f"{path} back to normal ({shown})", tag=path, value=value)

    def publish(self, with_schema=False):
        if not self.pending and not with_schema:
            return
        data = {"tags": self.pending}
        if with_schema:
            data["schema"] = self.schema
        key = (self.plc_id, TAGS_TOPIC)
        send(self.producer, TAGS_TOPIC, self.plc_id, envelope(self.plc_id, "plc", self.seq.next(key), data))
        self.last.update(self.pending)
        self.pending = {}

    async def browse(self, client):
        idx = await client.get_namespace_index(NAMESPACE)
        root = client.get_node(ua.NodeId(self.plc_id, idx))
        variables = []

        async def walk(node, prefix):
            for child in await node.get_children(refs=ua.ObjectIds.Organizes):
                name = (await child.read_browse_name()).Name
                await walk(child, f"{prefix}.{name}" if prefix else name)
            for var in await node.get_children(refs=ua.ObjectIds.HasComponent,
                                               nodeclassmask=ua.NodeClass.Variable):
                name = (await var.read_browse_name()).Name
                variables.append((f"{prefix}.{name}" if prefix else name, var))

        await walk(root, "")
        self.paths, self.schema = {}, {}
        for path, var in variables:
            props = {}
            for prop in await var.get_properties():
                props[(await prop.read_browse_name()).Name] = await prop.read_value()
            vtype = await var.read_data_type_as_variant_type()
            self.paths[var.nodeid] = path
            self.schema[path] = {
                "type": vtype.name,
                "unit": props.get("Unit", ""),
                "enum": json.loads(props.get("Enum") or "{}"),
                "alarm": json.loads(props.get("AlarmLimits") or "{}"),
            }
        return [var for _, var in variables]

    def went_offline(self, reason):
        if self.online is not False:
            self.event("plc_offline", "high", f"{self.plc_id} unreachable: {reason}")
            # Last known values are no longer trustworthy.
            self.pending = {p: {**v, "q": "BadNoCommunication"} for p, v in self.last.items()}
            self.publish()
        self.online = False

    async def run(self, stop):
        backoff = 1
        while not stop.is_set():
            try:
                async with Client(url=self.url, timeout=4) as client:
                    variables = await self.browse(client)
                    sub = await client.create_subscription(PUBLISH_MS, self)
                    await sub.subscribe_data_change(variables)  # initial values arrive as notifications
                    await asyncio.sleep(PUBLISH_MS / 1000)
                    self.publish(with_schema=True)
                    self.online = True
                    self.event("plc_online", "info", f"{self.plc_id} connected, {len(variables)} tags",
                               tags=len(variables))
                    backoff = 1
                    while not stop.is_set():
                        await asyncio.sleep(PUBLISH_MS / 1000)
                        self.publish()
                        await client.check_connection()
            except Exception as e:  # any connection/protocol failure: mark offline and retry
                self.went_offline(f"{type(e).__name__}: {e}"[:200])
            if not stop.is_set():
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10)


async def main():
    logging.basicConfig(level=logging.ERROR)
    producer = make_producer(BOOTSTRAP, "opcua-gateway")
    seq = Seq()
    links = [PlcLink(plc_id, url, producer, seq) for plc_id, url in PLC_ENDPOINTS.items()]
    print(f"[gateway] {', '.join(f'{lk.plc_id}={lk.url}' for lk in links)} -> {TAGS_TOPIC} via {BOOTSTRAP}",
          flush=True)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async def serve_callbacks():
        while not stop.is_set():
            producer.poll(0)
            await asyncio.sleep(0.1)

    await asyncio.gather(serve_callbacks(), *(lk.run(stop) for lk in links))
    producer.flush(10)


if __name__ == "__main__":
    asyncio.run(main())
