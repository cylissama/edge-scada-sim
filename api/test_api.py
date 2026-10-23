import json

import app as api
import pytest
from fastapi.testclient import TestClient
from live import Live


def deployment(name, role="device", replicas=1, ready=1, controllable=True, tier="edge"):
    return {
        "metadata": {"name": name, "labels": {"scada/controllable": "true"} if controllable else {}},
        "spec": {"replicas": replicas, "template": {"metadata": {"labels": {"app": name, "role": role, "kind": "imu"}},
                                                   "spec": {"nodeSelector": {"tier": tier}}}},
        "status": {"readyReplicas": ready},
    }


def test_status_rules():
    now = 1000.0
    topo = api.build_topology(
        [deployment("imu-1"), deployment("imu-2"), deployment("imu-3", replicas=0, ready=0),
         deployment("imu-4", ready=0), deployment("historian", role="core-service", controllable=False, tier="core")],
        [], [], last_seen={"imu-1": now - 1, "imu-2": now - 10}, plc_bad={}, now=now)
    status = {c["id"]: c["status"] for c in topo}
    assert status == {"imu-1": "ok", "imu-2": "stale", "imu-3": "stopped", "imu-4": "down", "historian": "ok"}
    assert [c["tier"] for c in topo] == ["edge"] * 4 + ["core"]


class FakeKube:
    def __init__(self):
        self.scaled = []

    def deployment(self, name):
        return deployment(name, controllable=name != "kafka")

    def scale(self, name, replicas):
        self.scaled.append((name, replicas))


def test_control_refuses_non_controllable(monkeypatch):
    fake = FakeKube()
    monkeypatch.setattr(api, "kube", fake)
    monkeypatch.setattr(api, "send", lambda *a: None)
    client = TestClient(api.app)  # no "with": lifespan (Kafka threads) does not start
    assert client.post("/api/components/imu-2/stop").json() == {"id": "imu-2", "desired": 0}
    assert client.post("/api/components/kafka/stop").status_code == 403
    assert client.post("/api/components/imu-2/reboot").status_code == 422
    assert fake.scaled == [("imu-2", 0)]


@pytest.fixture
def live():
    lv = Live(loop=None)
    lv.sent = []
    lv.publish = lambda event, data: lv.sent.append(event)
    return lv


def msg(kind, device, data):
    return json.dumps({"device_id": device, "kind": kind, "boot_id": "b", "seq": 0,
                       "ts": "2026-10-23T18:00:00Z", "data": data}).encode()


def test_live_downsamples_motion_but_not_plc_or_events(live):
    for i in range(10):  # 10 IMU messages within 0.1 s -> 1 forwarded at 5 Hz
        live.handle(msg("imu", "imu-1", {}), now=100 + i * 0.01)
    for i in range(3):
        live.handle(msg("plc", "plc-1", {"tags": {"A": {"v": i, "q": "Good"}}}), now=100 + i * 0.01)
    live.handle(msg("event", "plc-1", {"type": "alarm"}), now=101)
    assert live.sent == ["imu", "plc", "plc", "plc", "event"]
    assert live.last_seen["opcua-gateway"] == 100.02 and live.plc["plc-1"]["values"]["A"]["v"] == 2


def test_plc_bad_quality_tracked(live):
    live.handle(msg("plc", "plc-1", {"tags": {"A": {"v": 1, "q": "BadNoCommunication"}}}))
    assert live.plc_bad["plc-1"]
    live.handle(msg("plc", "plc-1", {"tags": {"A": {"v": 1, "q": "Good"}}}))
    assert not live.plc_bad["plc-1"]
