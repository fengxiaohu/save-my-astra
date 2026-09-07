from __future__ import annotations

from typing import Literal

DelegationExpected = Literal["true", "false", "optional"]
RoutingStatus = Literal["routing_ok", "routing_missed", "over_delegated"]


def normalize_expected(value: str | bool | None) -> DelegationExpected:
    if value is True or value == "true":
        return "true"
    if value is False or value == "false":
        return "false"
    return "optional"


def classify_routing(
    delegation_expected: str | bool | None,
    spawn_count: int,
) -> RoutingStatus:
    """spawn=0 is not a failure unless the item required a child."""
    expected = normalize_expected(delegation_expected)
    if expected == "true" and spawn_count == 0:
        return "routing_missed"
    if expected == "false" and spawn_count > 0:
        return "over_delegated"
    return "routing_ok"
