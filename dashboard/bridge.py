"""Kafka consumer that keeps the latest reading per sensor in memory.

Runs in a background thread so the HTTP server stays responsive.
"""
import json
import os
import threading
import time

from confluent_kafka import Consumer, KafkaError

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
TOPIC = os.getenv("KAFKA_TOPIC", "sensor-readings")
GROUP_ID = os.getenv("KAFKA_GROUP_ID", "dashboard")
TOPICS = [TOPIC, "imu-readings"]

_lock = threading.Lock()
_latest = {}  # sensor_id -> most recent reading (plus Kafka metadata)
_stats = {"messages": 0, "errors": 0, "started_at": time.time()}


def _consume_loop():
    consumer = Consumer({
        "bootstrap.servers": BOOTSTRAP,
        "group.id": GROUP_ID,
        # On first start (no committed offset), read from the beginning of the log.
        "auto.offset.reset": "earliest",
        "enable.auto.commit": True,
    })
    consumer.subscribe(TOPICS)
    print(f"[bridge] subscribed to {TOPICS} as group '{GROUP_ID}'", flush=True)

    while True:
        msg = consumer.poll(1.0)
        if msg is None:
            continue
        if msg.error():
            with _lock:
                _stats["errors"] += 1
            print(f"[bridge] consumer error: {msg.error()}", flush=True)
            continue

        try:
            reading = json.loads(msg.value())
        except (json.JSONDecodeError, TypeError):
            with _lock:
                _stats["errors"] += 1
            continue

        # Attach Kafka metadata so the table shows where each message came from.
        reading["partition"] = msg.partition()
        reading["offset"] = msg.offset()
        reading["received_at"] = time.time()

        # Normalize to device_id; handle both sensor_id and device_id
        device_id = reading.get("device_id") or reading.get("sensor_id")
        if not device_id:
            continue

        with _lock:
            _latest[device_id] = reading
            _stats["messages"] += 1


def start():
    thread = threading.Thread(target=_consume_loop, name="kafka-consumer", daemon=True)
    thread.start()


def snapshot():
    with _lock:
        sensors = sorted(_latest.values(), key=lambda r: r.get("device_id") or r.get("sensor_id"))
        stats = dict(_stats)
    return {"sensors": sensors, "stats": stats, "topic": TOPIC, "group": GROUP_ID}
