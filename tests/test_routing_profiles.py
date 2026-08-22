"""통행 판정 — 모르는 것을 통행 가능으로 두지 않는가.

원칙은 datasets 레포가 적어 둔 그대로다.
> 통행 가능한 길을 빼는 쪽이 막힌 길로 보내는 쪽보다 안전하다.
"""

from __future__ import annotations

import pytest

from app.routing.profiles import TransportMode, profile_for


@pytest.mark.parametrize(
    ("mode", "tags", "expected"),
    [
        (TransportMode.FOOT, {"highway": "residential"}, True),
        (TransportMode.FOOT, {"highway": "footway"}, True),
        (TransportMode.FOOT, {"highway": "steps"}, True),
        # 자동차 전용도로는 걸어서 갈 수 없다
        (TransportMode.FOOT, {"highway": "motorway"}, False),
        (TransportMode.CAR, {"highway": "motorway"}, True),
        (TransportMode.CAR, {"highway": "footway"}, False),
        # 휠체어·보행보조는 계단과 비포장을 쓸 수 없다
        (TransportMode.ASSISTED, {"highway": "steps"}, False),
        (TransportMode.ASSISTED, {"highway": "track"}, False),
        (TransportMode.ASSISTED, {"highway": "residential"}, True),
        (TransportMode.BICYCLE, {"highway": "cycleway"}, True),
    ],
)
def test_grade_rules(mode, tags, expected):
    allowed, _ = profile_for(mode).passable(tags)
    assert allowed is expected


def test_emergency_access_is_not_usable_by_residents():
    """긴급차량 전용은 주민 자가 대피에 쓸 수 없다."""
    allowed, reason = profile_for(TransportMode.CAR).passable(
        {"highway": "residential", "access": "emergency"}
    )
    assert not allowed
    assert "emergency" in reason


@pytest.mark.parametrize("value", ["private", "no", "agricultural", "military"])
def test_blocking_access_values(value):
    allowed, _ = profile_for(TransportMode.CAR).passable(
        {"highway": "track", "motor_vehicle": value}
    )
    assert not allowed


def test_conditional_restrictions_are_excluded_without_interpreting_them():
    """`no @ (wet)` 를 통행 가능으로 두면 호우 상황에서 위험하다."""
    allowed, reason = profile_for(TransportMode.CAR).passable(
        {"highway": "residential", "access:conditional": "no @ (wet)"}
    )
    assert not allowed
    assert "conditional" in reason


def test_ford_is_blocked_by_default():
    """세월교는 호우 시 가장 먼저 끊긴다."""
    allowed, reason = profile_for(TransportMode.FOOT).passable({"highway": "track", "ford": "yes"})
    assert not allowed
    assert "세월교" in reason


def test_impassable_smoothness_is_excluded():
    allowed, _ = profile_for(TransportMode.FOOT).passable(
        {"highway": "path", "smoothness": "impassable"}
    )
    assert not allowed


def test_unreliable_surface_only_blocks_strict_modes():
    """보행보조는 비포장을 못 쓰지만, 도보는 쓸 수 있다."""
    tags = {"highway": "residential", "surface": "mud"}
    assert profile_for(TransportMode.FOOT).passable(tags)[0] is True
    assert profile_for(TransportMode.ASSISTED).passable(tags)[0] is False


def test_missing_highway_tag_is_not_passable():
    allowed, reason = profile_for(TransportMode.FOOT).passable({"name": "어딘가"})
    assert not allowed
    assert "highway" in reason


def test_reason_is_always_given_when_blocked():
    """화면이 '왜 못 가는지'를 말해야 한다 — 경로가 없다는 것만으로는 판단할 수 없다."""
    for mode in TransportMode:
        allowed, reason = profile_for(mode).passable({"highway": "proposed"})
        assert not allowed
        assert reason


def test_assisted_is_slower_than_walking():
    """이동지원이 필요한 주민이 더 오래 걸린다는 사실이 경로 시간에 반영돼야 한다."""
    assert profile_for(TransportMode.ASSISTED).speed_mps < profile_for(TransportMode.FOOT).speed_mps
