"""Simulated overhead camera reporting ArUco marker poses to Kafka.

The camera looks straight down at a conveyor loop. Markers ride the loop, a
rounded rectangle in the plane at DEPTH metres, evenly spaced and moving at
SPEED m/s. Poses use OpenCV's camera frame (x right, y down, z forward) and
ArUco's conventions:
  - tvec: marker centre in metres
  - rvec: Rodrigues rotation vector (axis * angle, radians) of the marker frame.
    The marker faces the camera (rotated 180 deg about x), with its in-plane
    yaw following the direction of travel, plus small roll/pitch wobble.
Markers outside the field of view, or in a random ~1.5% of frames, are
reported as visible=false with no pose, as a real detector would miss them.
Pose noise grows with distance squared, as it does for real ArUco detections.

One message per frame:
    data = {"frame": n, "markers": [{"marker_id", "visible", "tvec", "rvec"}, ...]}
"""
import math
import os
import random
import signal
import time

from telemetry import envelope, make_producer, send

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
TOPIC = os.getenv("KAFKA_TOPIC", "camera-poses")
DEVICE_ID = os.getenv("DEVICE_ID", "camera-1")
FPS = float(os.getenv("FPS", "15"))
MARKERS = [int(m) for m in os.getenv("MARKERS", "1,2,3").split(",")]
PATH_W = float(os.getenv("PATH_W", "2.4"))       # loop width (m)
PATH_H = float(os.getenv("PATH_H", "1.2"))       # loop height (m)
CORNER_R = float(os.getenv("CORNER_R", "0.3"))   # corner radius (m)
SPEED = float(os.getenv("SPEED", "0.25"))        # m/s along the loop
DEPTH = float(os.getenv("DEPTH", "1.5"))         # camera-to-conveyor distance (m)
HFOV = math.radians(float(os.getenv("HFOV_DEG", "70")))
VFOV = math.radians(float(os.getenv("VFOV_DEG", "50")))
DROPOUT = float(os.getenv("DROPOUT", "0.015"))

running = True


def _request_stop(signum, frame):
    global running
    running = False


signal.signal(signal.SIGTERM, _request_stop)
signal.signal(signal.SIGINT, _request_stop)


def loop_segments(w, h, r):
    """Rounded rectangle centred on the optical axis, traversed clockwise in the image.

    Returns [(length, fn(u) -> (x, y, heading))] where u is the distance along the segment.
    """
    sx, sy = w - 2 * r, h - 2 * r          # straight lengths
    hx, hy = w / 2 - r, h / 2 - r          # corner-centre offsets

    def line(x0, y0, heading):
        return lambda u: (x0 + u * math.cos(heading), y0 + u * math.sin(heading), heading)

    def arc(cx, cy, start):
        # Clockwise in image coords (y down) means increasing angle.
        return lambda u: (cx + r * math.cos(start + u / r), cy + r * math.sin(start + u / r),
                          start + u / r + math.pi / 2)

    return [
        (sx, line(-hx, -h / 2, 0.0)),                  # top edge, moving +x
        (math.pi * r / 2, arc(hx, -hy, -math.pi / 2)),
        (sy, line(w / 2, -hy, math.pi / 2)),           # right edge, moving +y (down)
        (math.pi * r / 2, arc(hx, hy, 0.0)),
        (sx, line(hx, h / 2, math.pi)),                # bottom edge, moving -x
        (math.pi * r / 2, arc(-hx, hy, math.pi / 2)),
        (sy, line(-w / 2, hy, -math.pi / 2)),          # left edge, moving -y (up)
        (math.pi * r / 2, arc(-hx, -hy, math.pi)),
    ]


SEGMENTS = loop_segments(PATH_W, PATH_H, CORNER_R)
PERIMETER = sum(length for length, _ in SEGMENTS)


def point_on_loop(s):
    s %= PERIMETER
    for length, fn in SEGMENTS:
        if s <= length:
            return fn(s)
        s -= length
    return SEGMENTS[-1][1](SEGMENTS[-1][0])


def rotation_to_rvec(m):
    """3x3 rotation matrix -> Rodrigues vector, via a quaternion (stable near 180 deg)."""
    tr = m[0][0] + m[1][1] + m[2][2]
    if tr > 0:
        s = math.sqrt(tr + 1) * 2
        w, x, y, z = s / 4, (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1 + m[0][0] - m[1][1] - m[2][2]) * 2
        w, x, y, z = (m[2][1] - m[1][2]) / s, s / 4, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1 + m[1][1] - m[0][0] - m[2][2]) * 2
        w, x, y, z = (m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s, s / 4, (m[1][2] + m[2][1]) / s
    else:
        s = math.sqrt(1 + m[2][2] - m[0][0] - m[1][1]) * 2
        w, x, y, z = (m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, s / 4
    norm = math.sqrt(x * x + y * y + z * z)
    if norm < 1e-12:
        return (0.0, 0.0, 0.0)
    angle = 2 * math.atan2(norm, w)
    return (x / norm * angle, y / norm * angle, z / norm * angle)


def matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return [[1, 0, 0], [0, c, -s], [0, s, c]]


def rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, 0, s], [0, 1, 0], [-s, 0, c]]


def rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def in_view(x, y, z):
    return abs(x) <= z * math.tan(HFOV / 2) and abs(y) <= z * math.tan(VFOV / 2)


def observe(t):
    """Return the marker list for time t (seconds)."""
    markers = []
    for i, marker_id in enumerate(MARKERS):
        s = SPEED * t + i * PERIMETER / len(MARKERS)
        x, y, heading = point_on_loop(s)
        z = DEPTH + 0.01 * math.sin(2 * math.pi * 0.3 * t + i)   # belt flutter
        if not in_view(x, y, z) or random.random() < DROPOUT:
            markers.append({"marker_id": marker_id, "visible": False, "tvec": None, "rvec": None})
            continue
        wobble = 0.02 * math.sin(2 * math.pi * 0.7 * t + i)
        rot = matmul(rot_z(heading), matmul(rot_x(math.pi + wobble), rot_y(wobble / 2)))
        sigma = 0.0015 * z * z
        tvec = [x + random.gauss(0, sigma), y + random.gauss(0, sigma), z + random.gauss(0, 3 * sigma)]
        rvec = [v + random.gauss(0, 0.005) for v in rotation_to_rvec(rot)]
        markers.append({
            "marker_id": marker_id,
            "visible": True,
            "tvec": dict(zip("xyz", (round(v, 4) for v in tvec), strict=True)),
            "rvec": dict(zip("xyz", (round(v, 4) for v in rvec), strict=True)),
        })
    return markers


def main():
    producer = make_producer(BOOTSTRAP, DEVICE_ID)
    period = 1.0 / FPS
    print(f"[{DEVICE_ID}] markers {MARKERS} on a {PATH_W}x{PATH_H} m loop at {SPEED} m/s, "
          f"{FPS} fps -> '{TOPIC}' via {BOOTSTRAP}", flush=True)

    frame = 0
    next_tick = time.monotonic()
    try:
        while running:
            data = {"frame": frame, "markers": observe(frame * period)}
            send(producer, TOPIC, DEVICE_ID, envelope(DEVICE_ID, "camera", frame, data))
            frame += 1
            next_tick += period
            sleep_for = next_tick - time.monotonic()
            if sleep_for > 0:
                time.sleep(sleep_for)
    finally:
        print(f"[{DEVICE_ID}] flushing and stopping", flush=True)
        producer.flush(10)


if __name__ == "__main__":
    main()
