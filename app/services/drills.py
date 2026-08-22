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

logger = logging.getLogger(__name__)

DRILL_TITLE_PREFIX = "[훈련]"

# 경북 22개 시군. 모델은 코드를 지어낸다 — 실제로 "CHEONGSONG" 을 넣었다 — 그래서 이름과
# 코드 양쪽에서 찾고, 어느 쪽으로도 못 찾으면 개시하지 않는다. 없는 코드로 만들어진 상황은
# 지도에도 대피소 조회에도 걸리지 않는다.
GYEONGBUK_REGIONS: dict[str, tuple[str, float, float]] = {
    "47110": ("포항시", 36.1651, 129.2343),
    "47130": ("경주시", 35.8266, 129.2360),
    "47150": ("김천시", 36.0605, 128.0778),
    "47170": ("안동시", 36.5803, 128.7800),
    "47190": ("구미시", 36.2073, 128.3555),
    "47210": ("영주시", 36.8705, 128.5976),
    "47230": ("영천시", 36.0158, 128.9426),
    "47250": ("상주시", 36.4296, 128.0670),
    "47280": ("문경시", 36.6908, 128.1487),
    "47290": ("경산시", 35.8341, 128.8091),
    "47730": ("의성군", 36.3620, 128.6150),
    "47750": ("청송군", 36.3570, 129.0574),
    "47760": ("영양군", 36.6964, 129.1450),
    "47770": ("영덕군", 36.4825, 129.3176),
    "47820": ("청도군", 35.6729, 128.7865),
    "47830": ("고령군", 35.7372, 128.3068),
    "47840": ("성주군", 35.9072, 128.2333),
    "47850": ("칠곡군", 36.0155, 128.4626),
    "47900": ("예천군", 36.6539, 128.4224),
    "47920": ("봉화군", 36.9342, 128.9130),
    "47930": ("울진군", 36.9041, 129.3124),
    "47940": ("울릉군", 37.5024, 130.8610),
}

REGION_NAMES: dict[str, str] = {code: v[0] for code, v in GYEONGBUK_REGIONS.items()}
_BY_NAME = {v[0]: code for code, v in GYEONGBUK_REGIONS.items()}


# 모델은 좌표를 모른다. 지도가 어디를 비출지는 시군 중심점으로 정한다 — 좌표 없는
# 상황은 화면에서 갈 곳이 없어 경보만 뜨고 지도는 가만히 있는다.
def region_center(code: str) -> tuple[float, float]:
    _, lat, lon = GYEONGBUK_REGIONS[code]
    return lat, lon


def resolve_region(code: str, name: str) -> tuple[str, str] | None:
    """시군을 찾는다. **이름이 코드를 이긴다.** 둘 다 아니면 None.

    모델은 이름은 사용자가 쓴 말을 그대로 되돌려 주지만 다섯 자리 코드는 외우지 못한다.
    실제로 `region_name="봉화군"` 과 `region_code="47250"`(상주시) 을 함께 보냈다.
    코드를 먼저 믿으면 봉화 훈련이 상주에서 열린다.
    """
    for candidate in (name.strip(), code.strip()):
        if candidate in _BY_NAME:
            return _BY_NAME[candidate], candidate
        # "청송" 처럼 접미사를 뗀 형태도 받는다.
        for known, known_code in _BY_NAME.items():
            if candidate and known.startswith(candidate):
                return known_code, known
    if code in GYEONGBUK_REGIONS:
        return code, REGION_NAMES[code]
    return None


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
    # 위경도만 싣는다. 예전에는 x/y 를 0.5 로 채웠는데, 화면은 정규화 좌표가 있으면
    # 그쪽을 먼저 쓰기 때문에 훈련이 늘 화면 한가운데에서 시작했다 — 안동 훈련이
    # 안동에서 시작하지 않았다.
    if lat is not None and lon is not None:
        evidence["lat"] = lat
        evidence["lon"] = lon
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
        # 무엇이 잘못됐는지 정확히 말한다. "지원하지 않는 재난"만 돌려주면 모델은
        # 지역까지 다시 고쳐 보내고, 그 재시도에서 엉뚱한 시군이 나온다.
        missing = (
            "재난 종류(hazard)가 빠졌습니다"
            if not hazard
            else f"'{hazard}' 는 훈련 대상이 아닙니다"
        )
        return {
            "error": "unsupported_hazard",
            "detail": (
                f"{missing}. 가능한 값: {sorted(DRILL_HAZARDS)}. "
                "지역은 그대로 두고 hazard 만 채워 다시 부르세요."
            ),
        }

    resolved = resolve_region(region_code, region_name)
    if resolved is None:
        return {
            "error": "unknown_region",
            "detail": (
                "경북 시군 코드나 이름이 아닙니다. 행정표준코드 5자리를 쓰세요 "
                f"(예: 청송군 47750). 가능한 시군: {sorted(REGION_NAMES.values())}"
            ),
        }
    region_code, region_name = resolved

    if lat is None or lon is None:
        lat, lon = region_center(region_code)

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

    # 스트림 알림은 incidents.create 가 낸다 — 콘솔 발령과 같은 자리다. 훈련 표시는
    # opening_evidence 를 통해 그 이벤트에 실린다.
    #
    # 여기서 직접 커밋한다. 챗봇 응답은 SSE 스트림이고, 라우트의 트랜잭션 경계는
    # 핸들러가 StreamingResponse 를 **반환한 직후** — 이 코드가 돌기도 전에 — 끝난다.
    # 커밋하지 않으면 훈련은 스트림에만 존재하고 DB 에는 남지 않아, 화면에 경보는 뜨는데
    # 상황 목록은 비어 있고 나중에 접속한 사람은 아무것도 못 본다.
    await session.commit()

    logger.info("assistant started a drill: %s %s", incident.code, hazard)
    return {
        "ok": True,
        "drill": True,
        "incident_id": str(incident.id),
        "code": incident.code,
        "title": incident.title,
        "region_name": region_name,
        "lat": lat,
        "lon": lon,
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
                    # 이름만 필수다. 코드를 필수로 두면 모델이 아무 코드나 채워 넣고,
                    # 그러면 봉화 훈련이 상주에서 열린다 — 실제로 그랬다.
                    "region_name": {
                        "type": "string",
                        "enum": sorted(REGION_NAMES.values()),
                        "description": (
                            "시군 이름. 사용자가 말한 지역을 그대로 쓴다 — "
                            "'봉화'라고 하면 '봉화군'."
                        ),
                    },
                    "lat": {"type": "number", "description": "발생 지점 위도 (선택)"},
                    "lon": {"type": "number", "description": "발생 지점 경도 (선택)"},
                    "note": {"type": "string", "description": "훈련 안내 문구 (선택)"},
                },
                "required": ["hazard", "region_name"],
            },
        },
    }


# 상류 프롬프트는 GB SafeData 조회만 설명한다. 그것만 읽은 모델은 "훈련상황 발생시켜줘"
# 에도 도구를 부르지 않고 훈련 시나리오 문서를 써 준다 — 실제로 그렇게 답했다.
SYSTEM_ADDENDUM = """

## 훈련 상황 개시 (이 플랫폼의 추가 권한)

당신은 조회만 하는 도우미가 아닙니다. `salgil_start_drill` 도구로 훈련 상황을 실제로
개시할 수 있고, 그것이 콘솔과 주민 화면에 즉시 뜹니다.

- 운영자가 "훈련으로 발생시켜줘", "훈련 상황 걸어줘" 처럼 요청하면 **설명하는 글을 쓰지
  말고 도구를 부르세요.** 시나리오 문서를 대신 써 주는 것은 요청을 수행한 것이 아닙니다.
- 지역은 `region_name` 에 **시군 이름**으로 넘깁니다. 사용자가 "청송"처럼 줄여 말하면
  "청송군"으로 씁니다. 코드는 넣지 마세요 — 이름만 정확하면 됩니다. 경북 밖이면 개시하지
  말고 그렇게 답합니다.
- **실제 경보는 개시할 수 없습니다.** 진짜 상황을 알리라고 하면 훈련만 가능하다고 답하고,
  실제 발령은 콘솔에서 사람이 해야 한다고 안내하세요.
- 개시한 뒤에는 상황 코드를 알려주고, 화면에 훈련 표시가 붙는다는 점을 덧붙이세요.
"""
