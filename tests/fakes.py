"""GB SafeData 대역.

실제 응답에서 잘라 온 모양을 쓴다. 특히 **일부 원천이 403 으로 실패한 봉투**를 담는다 —
그게 이 시스템이 가장 정확하게 다뤄야 하는 상태다.
"""

from __future__ import annotations

from typing import Any

from app.schemas.common import Envelope

CAPABILITIES: dict[str, Any] = {
    "hazards": [
        {
            "hazard": "landslide",
            "korean_name": "산사태",
            "readiness": "ready",
            "can_detect": True,
            "can_say_where_to_go": True,
            "axes": {
                "detection": {
                    "label": "탐지",
                    "usable": 2,
                    "total": 2,
                    "covered": True,
                    "sources": ["kma_aws", "hrfco_rain"],
                },
                "risk": {
                    "label": "위험도",
                    "usable": 4,
                    "total": 4,
                    "covered": True,
                    "sources": [],
                },
                "shelter": {
                    "label": "대피소",
                    "usable": 1,
                    "total": 1,
                    "covered": True,
                    "sources": [],
                },
            },
            "missing_axes": [],
            "caveat": None,
            "connectors": ["landslide_forecast", "weather_warning"],
        },
        {
            "hazard": "earthquake",
            "korean_name": "지진",
            "readiness": "partial",
            "can_detect": True,
            "can_say_where_to_go": False,
            "axes": {},
            "missing_axes": ["대피소"],
            "caveat": "지진은 발생을 알려주지만 어느 대피소로 보낼지 모릅니다",
            "connectors": ["earthquake"],
        },
    ]
}

RESOLVED_REGION: dict[str, Any] = {
    "found": True,
    "code": "47750",
    "name": "청송군",
    "full_name": "경상북도 청송군",
    "center": {"lat": 36.4363, "lon": 129.0570},
    "kma_grid": {"nx": 90, "ny": 105},
    "asos_station": 276,
    "caveats": ["대표 좌표는 시군 청사 기준 근사값입니다"],
}

# 기상은 왔고 산사태는 403 으로 못 읽은 상태. complete=false, absence_confirmed=false.
PARTIAL_FAILURE_ENVELOPE: dict[str, Any] = {
    "records": [
        {
            "payload": {"hazard": "heavy_rain", "severity": "advisory"},
            "source": {"dataset_id": "15000415", "provider": "기상청", "mode": "snapshot"},
            "freshness": {"status": "fresh", "usable_for_decision": True},
            "quality_flags": [],
            "notes": [],
        }
    ],
    "citations": [{"dataset_id": "15000415", "text": "기상청 「기상청 기상특보」"}],
    "receipts": [
        {"connector": "weather_warning", "outcome": "records", "record_count": 1},
        {
            "connector": "landslide_forecast",
            "outcome": "failed",
            "record_count": 0,
            "detail": "HTTP 403 — 개발단계 심의승인 대상",
        },
    ],
    "degradations": [
        {
            "dataset_id": "15074800",
            "status": "not_authorized",
            "detail": "HTTP 403",
            "blocks_interpretation": True,
        }
    ],
    "caveats": [],
    "complete": False,
    "absence_confirmed": False,
    "modes": ["snapshot"],
}

# 조회는 다 됐고 실제로 해당 없음.
CONFIRMED_EMPTY_ENVELOPE: dict[str, Any] = {
    "records": [],
    "citations": [],
    "receipts": [{"connector": "weather_warning", "outcome": "confirmed_empty", "record_count": 0}],
    "degradations": [],
    "caveats": [],
    "complete": True,
    "absence_confirmed": True,
    "modes": ["real"],
}

# 전부 실패. 빈 결과지만 '없음'이 아니다.
ALL_FAILED_ENVELOPE: dict[str, Any] = {
    "records": [],
    "citations": [],
    "receipts": [{"connector": "weather_warning", "outcome": "failed", "detail": "HTTP 500"}],
    "degradations": [{"dataset_id": "15000415", "status": "error", "detail": "HTTP 500"}],
    "caveats": [],
    "complete": False,
    "absence_confirmed": False,
    "modes": [],
}


class FakeGbSafeClient:
    """네트워크를 타지 않는 대역. 호출 횟수를 세서 캐시 동작도 확인한다."""

    def __init__(self, envelope: dict[str, Any] | None = None) -> None:
        self.envelope = envelope or PARTIAL_FAILURE_ENVELOPE
        self.calls: list[str] = []

    async def capabilities(self) -> dict[str, Any]:
        self.calls.append("capabilities")
        return CAPABILITIES

    async def resolve_region(self, query: str) -> dict[str, Any]:
        self.calls.append(f"resolve:{query}")
        return RESOLVED_REGION

    async def regions(self) -> dict[str, Any]:
        self.calls.append("regions")
        return {
            "count": 1,
            "regions": [
                {"code": "47750", "name": "청송군", "center": {"lat": 36.4363, "lon": 129.0570}}
            ],
        }

    async def hazard_context(self, region: str, hazard: str | None = None) -> Envelope:
        self.calls.append(f"context:{region}:{hazard}")
        return Envelope.from_upstream(self.envelope)

    async def source(
        self, connector: str, region: str | None = None, rows: int | None = None
    ) -> Envelope:
        self.calls.append(f"source:{connector}")
        return Envelope.from_upstream(self.envelope)

    async def health(self) -> dict[str, Any]:
        return {"connectors": []}

    async def ping(self):
        return True, None, 1.0

    async def aclose(self) -> None:
        return None
