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
