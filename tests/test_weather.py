"""기상 스냅샷 — 하드코딩된 데모 값을 대체하려고 만든 것."""

from __future__ import annotations

import pytest

from app.schemas.common import Envelope
from tests.fakes import RESOLVED_REGION

WEATHER_ENVELOPE = {
    "records": [
        {
            "payload": {
                "kind": kind,
                "value": value,
                "unit": unit,
                "station": "격자 96,103",
                "is_forecast": False,
            },
            "source": {
                "dataset_name": "기상청 단기예보",
                "provider": "기상청",
                "attribution": "출처: 기상청 「기상청 단기예보」 (공공누리 제1유형)",
                "source_url": "https://www.data.go.kr/data/15084084/openapi.do",
            },
            "freshness": {
                "status": "fresh",
                "as_of": "2026-08-23T08:00:00+09:00",
                "usable_for_decision": True,
            },
        }
        for kind, value, unit in (
            ("temperature", 24.6, "℃"),
            ("humidity", 96.0, "%"),
            ("wind_speed", 0.4, "m/s"),
            ("wind_direction", 194.0, "deg"),
            ("rainfall_1h", 0.0, "mm"),
            ("precipitation_type", 0.0, "code"),
        )
    ],
    "citations": [{"text": "기상청 「기상청 단기예보」 · KOGL-1"}],
    "receipts": [{"connector": "weather_now", "outcome": "records", "record_count": 6}],
    "degradations": [],
    "caveats": ["관측지점이 마을에서 3.2km 떨어져 있습니다"],
    "complete": True,
    "absence_confirmed": True,
    "modes": ["real"],
}


class _WeatherClient:
    def __init__(self, envelope: dict) -> None:
        self.envelope = envelope

    async def resolve_region(self, query: str) -> dict:
        return RESOLVED_REGION

    async def source(self, connector: str, region=None, rows=None) -> Envelope:
        assert connector == "weather_now"
        return Envelope.from_upstream(self.envelope)


async def _snapshot(envelope: dict):
    from app.services import situation

    return await situation.weather(_WeatherClient(envelope), region_query="청송군")


async def test_headline_values_are_extracted():
    snapshot = await _snapshot(WEATHER_ENVELOPE)

    assert snapshot.temperature_c == pytest.approx(24.6)
    assert snapshot.humidity_pct == pytest.approx(96.0)
    assert snapshot.wind_speed_ms == pytest.approx(0.4)
    assert snapshot.wind_direction_deg == pytest.approx(194.0)
    assert snapshot.rainfall_1h_mm == pytest.approx(0.0)
    assert snapshot.state == "DATA"


async def test_every_reading_is_kept_not_only_the_headline():
    """precipitation_type 처럼 꺼내 두지 않은 관측도 버리지 않는다."""
    snapshot = await _snapshot(WEATHER_ENVELOPE)
    kinds = {r.kind for r in snapshot.readings}
    assert "precipitation_type" in kinds
    assert len(snapshot.readings) == 6


async def test_attribution_and_caveats_survive():
    """KOGL 출처 표기와 관측지점 거리는 화면에서 지우면 안 된다."""
    snapshot = await _snapshot(WEATHER_ENVELOPE)
    assert snapshot.attribution
    assert "기상청" in snapshot.attribution
    assert snapshot.source_url
    assert any("3.2km" in c for c in snapshot.caveats)


async def test_missing_values_are_null_not_zero():
    """값이 없을 때 0 을 채우면 화면이 '강수 없음'으로 읽는다."""
    envelope = dict(WEATHER_ENVELOPE, records=[])
    envelope["absence_confirmed"] = True
    snapshot = await _snapshot(envelope)

    assert snapshot.temperature_c is None
    assert snapshot.rainfall_1h_mm is None
    assert snapshot.readings == []


async def test_unreadable_upstream_is_unverified_not_clear_weather():
    """조회 실패를 '맑음'으로 그리면 안 된다."""
    envelope = {
        **WEATHER_ENVELOPE,
        "records": [],
        "complete": False,
        "absence_confirmed": False,
        "receipts": [{"connector": "weather_now", "outcome": "failed", "detail": "HTTP 500"}],
    }
    snapshot = await _snapshot(envelope)
    assert snapshot.state == "UNVERIFIED"
    assert snapshot.temperature_c is None


async def test_stale_readings_are_flagged_with_their_time():
    """오래된 값을 숨기지 않고 시각과 함께 내보낸다."""
    envelope = {
        **WEATHER_ENVELOPE,
        "records": [
            {
                **WEATHER_ENVELOPE["records"][0],
                "freshness": {
                    "status": "stale",
                    "as_of": "2026-08-21T16:10:00+09:00",
                    "usable_for_decision": False,
                },
            }
        ],
    }
    snapshot = await _snapshot(envelope)
    assert snapshot.stale is True
    assert snapshot.observed_at is not None
    assert snapshot.readings[0].stale is True


async def test_non_numeric_values_do_not_break_the_snapshot():
    envelope = {
        **WEATHER_ENVELOPE,
        "records": [
            {
                **WEATHER_ENVELOPE["records"][0],
                "payload": {"kind": "temperature", "value": "-", "unit": "℃"},
            }
        ],
    }
    snapshot = await _snapshot(envelope)
    assert snapshot.temperature_c is None
    assert snapshot.readings[0].value is None
