"""Shared message envelope and Kafka producer helpers for every edge service.

Every message on every topic has the same outer shape, so consumers (historian,
API) never need a per-device parser:

    {"device_id", "kind", "boot_id", "seq", "ts", "data"}

boot_id is random per process start. Together with seq it uniquely identifies a
message even after a restart resets seq to 0.
"""
import json
import uuid
from datetime import datetime, timezone

BOOT_ID = uuid.uuid4().hex[:12]


def envelope(device_id, kind, seq, data):
    return {
        "device_id": device_id,
        "kind": kind,
        "boot_id": BOOT_ID,
        "seq": seq,
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "data": data,
    }


def make_producer(bootstrap, client_id, **overrides):
    from confluent_kafka import Producer

    def on_delivery(err, msg):
        if err is not None:
            print(f"[{client_id}] delivery failed: {err}", flush=True)

    config = {
        "bootstrap.servers": bootstrap,
        "client.id": client_id,
        # Durability: wait for all in-sync replicas to confirm the write.
        "acks": "all",
        # Retries cannot reorder or duplicate messages within this producer session.
        "enable.idempotence": True,
        "linger.ms": 5,
        "on_delivery": on_delivery,
    }
    config.update(overrides)
    return Producer(config)


def send(producer, topic, key, message):
    value = json.dumps(message).encode()
    while True:
        try:
            producer.produce(topic, key=key.encode(), value=value)
            producer.poll(0)  # serve delivery callbacks without blocking
            return
        except BufferError:
            # Local send queue is full: serve callbacks so it drains, then retry.
            producer.poll(0.5)
