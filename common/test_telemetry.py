from datetime import datetime

from telemetry import BOOT_ID, envelope


def test_envelope_shape():
    msg = envelope("imu-1", "imu", 7, {"x": 1})
    assert set(msg) == {"device_id", "kind", "boot_id", "seq", "ts", "data"}
    assert msg["boot_id"] == BOOT_ID
    assert msg["ts"].endswith("Z")
    datetime.fromisoformat(msg["ts"].replace("Z", "+00:00"))  # parses as ISO-8601
    assert envelope("imu-1", "imu", 8, {})["boot_id"] == msg["boot_id"]
