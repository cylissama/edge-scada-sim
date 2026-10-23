import json
import random
from pathlib import Path

from profiles import Conveyor, RobotCell
from tags import load

CONFIG = Path(__file__).resolve().parents[1] / "k8s" / "config" / "plc-tags.json"


def run(profile, seconds, dt=0.1):
    for _ in range(int(seconds / dt)):
        profile.step(dt)


def test_shipped_config_loads():
    for plc_id in ("plc-1", "plc-2"):
        plc, tags = load(CONFIG, plc_id)
        assert plc["profile"] in ("conveyor", "robot_cell")
        assert "System.PLC_ON" in {t.path for t in tags}


def test_shorthand_and_nesting(tmp_path):
    cfg = tmp_path / "tags.json"
    cfg.write_text(json.dumps({"plc-x": {"tags": {
        "PLC_ON": 1, "ROBOT_ON": False, "Area": {"Temp": {"value": 3.5, "unit": "C"}}}}}))
    _, tags = load(cfg, "plc-x")
    by_path = {t.path: t for t in tags}
    assert by_path["PLC_ON"].type == "Int32" and by_path["PLC_ON"].value == 1
    assert by_path["ROBOT_ON"].type == "Boolean"
    assert by_path["Area.Temp"].type == "Float" and by_path["Area.Temp"].unit == "C"


def values_for(plc_id):
    _, tags = load(CONFIG, plc_id)
    return {t.path: t.value for t in tags}


def test_conveyor_reaches_setpoint_then_trips_on_overtemp():
    random.seed(1)
    p = Conveyor(values_for("plc-1"))
    p.chance = staticmethod(lambda per_hour, dt: False)  # no random disturbances
    run(p, 30)
    assert abs(p.v["Drive.Speed_Actual"] - 1450) < 20 and p.v["Drive.Running"]
    assert p.v["Drive.Motor_Current"] > 10
    p.v["Drive.Motor_Temp"] = 96.0
    run(p, 1)
    assert p.v["System.Fault_Code"] == 101
    run(p, 10)
    assert p.v["Drive.Speed_Actual"] == 0 and not p.v["Drive.Running"]


def test_robot_completes_cycles_and_pauses_on_light_curtain():
    random.seed(2)
    p = RobotCell(values_for("plc-2"))
    p.chance = staticmethod(lambda per_hour, dt: False)
    run(p, 100)
    assert p.v["Robot.Cycle_Count"] == 2 and 38 < p.v["Robot.Cycle_Time"] < 46
    assert p.v["Coolant.Pump_On"] and p.v["Coolant.Pressure"] > 4
    p.curtain = 5
    run(p, 0.5)
    assert p.v["Robot.State"] == 2 and not p.v["Safety.Light_Curtain_Clear"]
