from gateway import alarm_level


def test_alarm_levels():
    limits = {"hi": 80, "hihi": 90, "lo": 2}
    assert alarm_level(limits, 50) is None
    assert alarm_level(limits, 85) == "hi"
    assert alarm_level(limits, 95) == "hihi"
    assert alarm_level(limits, 1.5) == "lo"
    assert alarm_level({"ne": 0}, 101) == "ne" and alarm_level({"ne": 0}, 0) is None
    assert alarm_level({"ne": True}, False) == "ne" and alarm_level({"ne": True}, True) is None
    assert alarm_level({"hi": 1}, True) is None  # booleans never trip numeric limits


def test_deadband_stops_chatter():
    limits = {"lo": 2.0}  # default deadband 2 % -> clears above 2.04
    assert alarm_level(limits, 1.99) == "lo"
    assert alarm_level(limits, 2.02, active="lo") == "lo"
    assert alarm_level(limits, 2.05, active="lo") is None
    assert alarm_level({"hi": 80, "deadband": 5}, 76, active="hi") == "hi"
    assert alarm_level({"hi": 80, "deadband": 5}, 74, active="hi") is None
