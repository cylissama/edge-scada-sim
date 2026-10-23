"""Simulated 6-axis IMU (accelerometer + gyroscope) publishing to Kafka.

The device tilts back and forth about the Y axis at 0.5 Hz. The accelerometer
and gyroscope values are derived from that tilt, so they agree physically:
  - tilt angle:        theta(t) = AMPLITUDE * sin(2*pi*F*t)  (degrees)
  - accel x = g*sin(theta), accel z = g*cos(theta)  (gravity projected onto axes)
  - gyro y  = d(theta)/dt                             (degrees per second)
Sensor noise is added on top, like a real chip would produce.

The message key is the device ID, so each device's readings stay in order
within one partition.
"""
import math
import os
import random
import signal
import time

from telemetry import envelope, make_producer, send

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
TOPIC = os.getenv("KAFKA_TOPIC", "imu-readings")
DEVICE_ID = os.getenv("DEVICE_ID", "imu-1")
RATE_HZ = float(os.getenv("RATE_HZ", "10"))

G = 9.81                 # m/s^2
TILT_AMPLITUDE = 30.0    # degrees
TILT_FREQUENCY = 0.5     # Hz

ACCEL_NOISE = 0.05       # m/s^2 (1 sigma)
GYRO_NOISE = 0.5         # deg/s (1 sigma)

running = True


def _request_stop(signum, frame):
    global running
    running = False


signal.signal(signal.SIGTERM, _request_stop)
signal.signal(signal.SIGINT, _request_stop)


def simulate(t):
    """Return (accel, gyro) at time t in seconds, with sensor noise."""
    w = 2 * math.pi * TILT_FREQUENCY
    theta = math.radians(TILT_AMPLITUDE * math.sin(w * t))
    theta_dot = math.radians(TILT_AMPLITUDE * w * math.cos(w * t))  # rad/s

    accel = {
        "x": G * math.sin(theta) + random.gauss(0, ACCEL_NOISE),
        "y": random.gauss(0, ACCEL_NOISE),
        "z": G * math.cos(theta) + random.gauss(0, ACCEL_NOISE),
    }
    gyro = {
        "x": random.gauss(0, GYRO_NOISE),
        "y": math.degrees(theta_dot) + random.gauss(0, GYRO_NOISE),
        "z": random.gauss(0, GYRO_NOISE),
    }
    return accel, gyro


def main():
    producer = make_producer(BOOTSTRAP, DEVICE_ID)

    period = 1.0 / RATE_HZ
    print(f"[{DEVICE_ID}] publishing {RATE_HZ} Hz to '{TOPIC}' via {BOOTSTRAP}", flush=True)

    seq = 0
    next_tick = time.monotonic()
    try:
        while running:
            t = seq * period
            accel, gyro = simulate(t)
            data = {
                "accel_ms2": {k: round(v, 4) for k, v in accel.items()},
                "gyro_dps": {k: round(v, 4) for k, v in gyro.items()},
            }
            send(producer, TOPIC, DEVICE_ID, envelope(DEVICE_ID, "imu", seq, data))
            seq += 1

            # Pace to the target rate without drifting over time.
            next_tick += period
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
    finally:
        print(f"[{DEVICE_ID}] flushing and stopping", flush=True)
        producer.flush(10)


if __name__ == "__main__":
    main()
