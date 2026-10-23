"""Live side of the API: one Kafka consumer fanned out to every browser.

Live      consumes all topics (group "dashboard-live", always starting at the end of the
          log), tracks when each device was last heard from and the latest PLC tag
          values, and pushes Server-Sent Events to subscriber queues. IMU and camera
          streams are downsampled to LIVE_HZ per device; PLC changes and events pass
          through as they are.
KafkaStats  per-topic message rates (from end-offset deltas) and consumer-group lag.
"""
import asyncio
import json
import os
import threading
import time

from confluent_kafka import OFFSET_END, Consumer, ConsumerGroupTopicPartitions, KafkaException, TopicPartition
from confluent_kafka.admin import AdminClient, OffsetSpec

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
TOPICS = ["plc-tags", "imu-readings", "camera-poses", "scada-events"]
LIVE_GROUP = "dashboard-live"
LAG_GROUPS = ["historian", LIVE_GROUP]
LIVE_HZ = float(os.getenv("LIVE_HZ", "5"))
GATEWAY = os.getenv("GATEWAY_ID", "opcua-gateway")


def sse(event, data):
    return f"event: {event}\ndata: {data}\n\n"


class Live:
    def __init__(self, loop):
        self.loop = loop
        self.subscribers = set()
        self.last_seen = {}    # device id -> unix time of its last telemetry message
        self.plc_bad = {}      # plc id -> latest message carried a non-Good quality
        self.plc = {}          # plc id -> {"schema": {...}, "values": {tag: {v, q, ts}}}
        self.lock = threading.Lock()
        self._last_sent = {}

    # --- event loop side -------------------------------------------------
    def subscribe(self):
        q = asyncio.Queue(maxsize=1000)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q):
        self.subscribers.discard(q)

    def broadcast(self, event, data):
        line = sse(event, data)
        for q in list(self.subscribers):
            try:
                q.put_nowait(line)
            except asyncio.QueueFull:
                pass  # slow client: drop this update, the next ones still arrive

    def plc_snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.plc))  # deep copy

    def seed_plc(self, plc_id, schema, values):
        """Fill gaps from the historian (tags that haven't changed since we started)."""
        with self.lock:
            state = self.plc.setdefault(plc_id, {"schema": {}, "values": {}})
            state["schema"] = state["schema"] or schema
            for tag, v in values.items():
                state["values"].setdefault(tag, v)

    # --- consumer thread side --------------------------------------------
    def publish(self, event, data):
        self.loop.call_soon_threadsafe(self.broadcast, event, data)

    def handle(self, raw, now=None):
        now = now or time.time()
        try:
            env = json.loads(raw)
            device, kind = env["device_id"], env["kind"]
        except (ValueError, KeyError, TypeError):
            return
        text = raw.decode() if isinstance(raw, bytes) else raw
        if kind == "event":
            self.publish("event", text)
            return
        self.last_seen[device] = now
        if kind == "plc":
            self.last_seen[GATEWAY] = now
            data = env["data"]
            tags = data.get("tags", {})
            with self.lock:
                state = self.plc.setdefault(device, {"schema": {}, "values": {}})
                if "schema" in data:
                    state["schema"] = data["schema"]
                state["values"].update(tags)
            if tags:
                self.plc_bad[device] = any(v.get("q") != "Good" for v in tags.values())
            self.publish("plc", text)
            return
        if now - self._last_sent.get(device, 0) >= 1 / LIVE_HZ:
            self._last_sent[device] = now
            self.publish(kind, text)

    def consume_forever(self):
        consumer = Consumer({
            "bootstrap.servers": BOOTSTRAP,
            "group.id": LIVE_GROUP,
            "enable.auto.commit": True,   # committed offsets make our lag visible in the Kafka panel
            "auto.commit.interval.ms": 1000,
        })

        # Assign every partition ourselves instead of subscribing as a group member: a
        # group would split partitions between API replicas (including the old and new
        # pod during a rolling update), and each replica must see every device.
        # Start at the end, since a dashboard wants "now" and not a replay.
        # The group id is only used to commit offsets, so the Kafka panel can show our lag.
        # ponytail: partitions added later need an API restart to be picked up.
        while True:
            try:
                md = consumer.list_topics(timeout=10)
                break
            except KafkaException as e:
                print(f"[api] waiting for Kafka: {e}", flush=True)
        consumer.assign([TopicPartition(t, p, OFFSET_END) for t in TOPICS if t in md.topics
                         for p in md.topics[t].partitions])
        print(f"[api] live consumer on {TOPICS} (group {LIVE_GROUP}, {LIVE_HZ} Hz per device)", flush=True)
        while True:
            msg = consumer.poll(1.0)
            if msg is None or msg.error():
                continue
            self.handle(msg.value())


class KafkaStats:
    def __init__(self):
        self.admin = AdminClient({"bootstrap.servers": BOOTSTRAP})
        self.prev = None  # (time, {topic: total messages})

    def snapshot(self):
        md = self.admin.list_topics(timeout=5)
        tps = [TopicPartition(t, p) for t in TOPICS if t in md.topics for p in md.topics[t].partitions]
        futures = self.admin.list_offsets({tp: OffsetSpec.latest() for tp in tps}, request_timeout=5)
        ends = {(tp.topic, tp.partition): f.result().offset for tp, f in futures.items()}

        now = time.time()
        totals = {t: sum(o for (topic, _), o in ends.items() if topic == t) for t in TOPICS}
        dt = now - self.prev[0] if self.prev else None
        topics = [{
            "name": t,
            "partitions": len(md.topics[t].partitions) if t in md.topics else 0,
            "messages": totals[t],
            "rate": round((totals[t] - self.prev[1].get(t, 0)) / dt, 1) if dt else None,
        } for t in TOPICS]
        self.prev = (now, totals)

        lag = {}
        for group in LAG_GROUPS:
            result = self.admin.list_consumer_group_offsets([ConsumerGroupTopicPartitions(group)])[group].result()
            lag[group] = sum(max(0, ends.get((tp.topic, tp.partition), tp.offset) - tp.offset)
                             for tp in result.topic_partitions if tp.offset >= 0)
        rates = [t["rate"] for t in topics if t["rate"] is not None]
        return {"topics": topics, "rate_total": round(sum(rates), 1) if rates else None, "lag": lag}
