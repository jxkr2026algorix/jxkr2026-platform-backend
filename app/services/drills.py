"""챗봇이 실행할 수 있는 유일한 쓰기 동작 — 훈련 상황 개시.

**훈련만 가능하다.** 모델이 실제 경보를 울릴 수 있으면, 프롬프트 한 줄로 주민 전체에게
대피 지시가 나간다. 그 권한은 사람에게 남긴다.

개시된 상황에는 훈련 표시가 붙고, 그 표시는 스트림과 화면까지 그대로 간다. 훈련이
실제처럼 보이면 두 번째 훈련부터 아무도 움직이지 않고, 진짜 경보도 같이 무시된다.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.hazard import Hazard
from app.schemas.incident import IncidentCreate
from app.services import incidents
from app.services.events import Event, broker

logger = logging.getLogger(__name__)

DRILL_TITLE_PREFIX = "[훈련]"

# 챗봇이 쓸 수 있는 재난. 모델이 임의의 문자열을 넣어 스키마를 뚫지 못하게 막는다.
DRILL_HAZARDS: dict[str, str] = {
    "wildfire": "산불",
    "flood": "홍수",
    "landslide": "산사태",
    "heavy_rain": "호우",
    "earthquake": "지진",
    "typhoon": "태풍",
    "heavy_snow": "대설",
    "heatwave": "폭염",
}


def drill_evidence(lat: float | None, lon: float | None, requested_by: str) -> dict[str, Any]:
    """훈련 표시를 근거 스냅샷에 박는다. 여기서 빠지면 화면이 구분할 방법이 없다."""
    evidence: dict[str, Any] = {
        "source": "salgil-assistant",
        "mode": "training",
        "drill": True,
        "requested_by": requested_by,
    }
    if lat is not None and lon is not None:
        evidence["map_origin"] = {"x": 0.5, "y": 0.5, "label": "훈련 지점", "lat": lat, "lon": lon}
    return evidence


async def start_drill(
    session: AsyncSession,
    *,
    hazard: str,
    region_code: str,
    region_name: str,
    lat: float | None = None,
    lon: float | None = None,
    note: str | None = None,
    actor: str = "assistant",
) -> dict[str, Any]:
    """훈련 상황을 개시하고 스트림에 알린다."""
    if hazard not in DRILL_HAZARDS:
        return {
            "error": "unsupported_hazard",
            "detail": f"훈련으로 개시할 수 있는 재난: {sorted(DRILL_HAZARDS)}",
        }

    korean = DRILL_HAZARDS[hazard]
    incident = await incidents.create(
        session,
        IncidentCreate(
            title=f"{DRILL_TITLE_PREFIX} {region_name} {korean} 대응 훈련",
            region_code=region_code,
            hazard=Hazard(hazard),
            level=1,
            summary=note or f"{korean} 대응 절차 훈련입니다. 실제 상황이 아닙니다.",
            opening_evidence=drill_evidence(lat, lon, actor),
        ),
        actor=actor,
        region_name=region_name,
    )

    # 훈련 표시는 스트림 이벤트에도 실린다. 화면이 상황 목록만 보고 판단하지 않도록.
    broker.publish(
        Event(
            kind="incident.declared",
            data={
                "incident_id": str(incident.id),
                "code": incident.code,
                "title": incident.title,
                "hazard": hazard,
                "region_code": region_code,
                "region_name": region_name,
                "drill": True,
                "mode": "training",
                **({"lat": lat, "lon": lon} if lat is not None and lon is not None else {}),
            },
            incident_id=str(incident.id),
        )
    )
    logger.info("assistant started a drill: %s %s", incident.code, hazard)
    return {
        "ok": True,
        "drill": True,
        "incident_id": str(incident.id),
        "code": incident.code,
        "title": incident.title,
        "note": "훈련 상황으로 개시했습니다. 실제 경보가 아니며 화면에 훈련 표시가 붙습니다.",
    }


def tool_spec() -> dict[str, Any]:
    """모델에 넘길 function-calling 정의."""
    return {
        "type": "function",
        "function": {
            "name": "salgil_start_drill",
            "description": (
                "경북 특정 시군에 대응 훈련 상황을 개시한다. **훈련 전용이며 실제 경보를 "
                "울리지 않는다.** 사용자가 '훈련으로 발생시켜' 라고 명시적으로 요청했을 "
                "때만 쓴다. 개시된 상황에는 훈련 표시가 붙어 화면에 그렇게 보인다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "hazard": {
                        "type": "string",
                        "enum": sorted(DRILL_HAZARDS),
                        "description": "훈련할 재난 종류",
                    },
                    "region_code": {
                        "type": "string",
                        "description": "경북 시군 행정표준코드 (예: 청송군 47750)",
                    },
                    "region_name": {"type": "string", "description": "시군 이름"},
                    "lat": {"type": "number", "description": "발생 지점 위도 (선택)"},
                    "lon": {"type": "number", "description": "발생 지점 경도 (선택)"},
                    "note": {"type": "string", "description": "훈련 안내 문구 (선택)"},
                },
                "required": ["hazard", "region_code", "region_name"],
            },
        },
    }
