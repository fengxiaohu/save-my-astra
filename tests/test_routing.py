from eval.routing import classify_routing


def test_missed_only_when_expected_and_no_spawn():
    assert classify_routing("true", 0) == "routing_missed"
    assert classify_routing(True, 0) == "routing_missed"


def test_optional_zero_spawn_is_ok():
    assert classify_routing("optional", 0) == "routing_ok"
    assert classify_routing(None, 0) == "routing_ok"


def test_false_with_spawn_is_over_delegated():
    assert classify_routing("false", 1) == "over_delegated"
    assert classify_routing(False, 2) == "over_delegated"


def test_true_with_spawn_is_ok():
    assert classify_routing("true", 1) == "routing_ok"


def test_false_without_spawn_is_ok():
    assert classify_routing("false", 0) == "routing_ok"
