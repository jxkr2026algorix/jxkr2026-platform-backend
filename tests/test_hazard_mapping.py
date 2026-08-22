"""프론트엔드 시나리오명 ↔ 정규 재난 코드."""

from __future__ import annotations

import pytest

from app.schemas.hazard import Hazard, from_map_scenario, korean_name, map_scenario


@pytest.mark.parametrize(
    ("scenario", "hazard"),
    [
        ("rain", Hazard.HEAVY_RAIN),
        ("coldwave", Hazard.COLD_WAVE),
        ("snow", Hazard.HEAVY_SNOW),
        ("chemical", Hazard.CHEMICAL_ACCIDENT),
        ("landslide", Hazard.LANDSLIDE),
    ],
)
def test_frontend_scenario_names_resolve(scenario, hazard):
    assert from_map_scenario(scenario) is hazard


def test_round_trip_for_every_hazard():
    for hazard in Hazard:
        scenario = map_scenario(hazard)
        assert scenario is not None
        assert from_map_scenario(scenario) is hazard


def test_unknown_scenario_returns_none_not_guess():
    """모르는 값을 추측하지 않는다 — 틀린 재난으로 조회하면 틀린 대피소가 나온다."""
    assert from_map_scenario("volcano") is None


def test_korean_names():
    assert korean_name(Hazard.HEAVY_RAIN) == "호우"
    assert korean_name("landslide") == "산사태"
