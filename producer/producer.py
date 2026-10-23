"""Simulated temperature sensor that publishes JSON readings to Kafka.

Each container runs one instance with its own SENSOR_ID. The message key is
the sensor ID, so every reading from one sensor lands in the same partition
and stays in order. Readings from different sensors can interleave.
"""
import json
import os
import random
import signal
import time
from datetime import datetime, timezone

from confluent_kafka import Producer

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
TOPIC = os.getenv("KAFKA_TOPIC", "sensor-readings")
SENSOR_ID = os.getenv("SENSOR_ID", "sensor-1")
BASE_TEMP = float(os.getenv("BASE_TEMP", "21.0"))
INTERVAL = float(os.getenv("INTERVAL_SECONDS", "1.0"))

running = True


def _request_stop(signum, frame):
    global running
    running = False


signal.signal(signal.SIGTERM, _request_stop)
signal.signal(signal.SIGINT, _request_stop)


def on_delivery(err, msg):
    # Called from producer.poll() once the broker has acknowledged (or refused) a message.
    if err is not None:
        print(f"[{SENSOR_ID}] delivery failed: {err}", flush=True)


def send(producer, reading):
    key = SENSOR_ID.encode()
    value = json.dumps(reading).encode()
    while True:
        try:
            producer.produce(TOPIC, key=key, value=value, on_delivery=on_delivery)
            return
        except BufferError:
            # Local send queue is full: serve callbacks so it drains, then retry.
            producer.poll(0.5)


def main():
    producer = Producer({
        "bootstrap.servers": BOOTSTRAP,
        "client.id": SENSOR_ID,
        # Durability: wait for the broker to confirm the write.
        "acks": "all",
        # Retries cannot reorder or duplicate messages within this producer session.
        "enable.idempotence": True,
        "linger.ms": 10,
    })

    print(f"[{SENSOR_ID}] publishing to '{TOPIC}' via {BOOTSTRAP}", flush=True)

    temp = BASE_TEMP
    seq = 0
    try:
        while running:
            # Random walk that drifts back toward the sensor's base temperature.
            temp += random.gauss(0, 0.3) + 0.1 * (BASE_TEMP - temp)
            reading = {
                "sensor_id": SENSOR_ID,
                "seq": seq,
                "temperature_c": round(temp, 2),
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            send(producer, reading)
            producer.poll(0)  # serve delivery callbacks without blocking
            seq += 1
            time.sleep(INTERVAL)
    finally:
        print(f"[{SENSOR_ID}] flushing and stopping", flush=True)
        producer.flush(10)


if __name__ == "__main__":
    main()
