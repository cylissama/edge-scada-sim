from historian import Transformer


def env(kind, device, seq, data, boot="b1"):
    return {"device_id": device, "kind": kind, "boot_id": boot, "seq": seq,
            "ts": "2026-10-23T18:00:00.000Z", "data": data}


def test_plc_tags_become_one_doc_per_tag_with_units_from_schema():
    tf = Transformer()
    msg = env("plc", "plc-1", 0, {
        "schema": {"Drive.Motor_Temp": {"type": "Float", "unit": "°C"}},
        "tags": {"Drive.Motor_Temp": {"v": 41.5, "q": "Good", "ts": "2026-10-23T17:59:59.900Z"},
                 "System.PLC_ON": {"v": True, "q": "Good", "ts": None}},
    })
    out, dev = tf.transform(msg)
    temp = next(d for d in out["plc_tags"] if d["meta"]["tag"] == "Drive.Motor_Temp")
    assert temp["meta"] == {"plc": "plc-1", "tag": "Drive.Motor_Temp", "unit": "°C"}
    assert temp["value"] == 41.5 and temp["ts"].second == 59
    assert dev["_id"] == "plc-1" and "tags" in dev


def test_camera_doc_per_marker_and_events_skip_device_registry():
    tf = Transformer()
    out, _ = tf.transform(env("camera", "camera-1", 5, {"frame": 5, "markers": [
        {"marker_id": 1, "visible": True, "tvec": {"x": 0, "y": 0, "z": 1}, "rvec": {"x": 3.1, "y": 0, "z": 0}},
        {"marker_id": 2, "visible": False, "tvec": None, "rvec": None}]}))
    assert [d["meta"]["marker_id"] for d in out["camera_poses"]] == [1, 2]
    out, dev = tf.transform(env("event", "plc-1", 0, {"type": "alarm", "severity": "high"}))
    assert out["events"][0]["type"] == "alarm" and dev is None


def test_replays_are_dropped_but_restarts_are_not():
    tf = Transformer()
    assert not tf.is_duplicate("imu-readings", env("imu", "imu-1", 10, {}))
    assert tf.is_duplicate("imu-readings", env("imu", "imu-1", 10, {}))          # replayed
    assert tf.is_duplicate("imu-readings", env("imu", "imu-1", 9, {}))
    assert not tf.is_duplicate("imu-readings", env("imu", "imu-1", 0, {}, boot="b2"))  # device restarted
