"""재난 유형 — 상류 값, 한국어 이름, 프론트엔드 맵 시나리오명의 단일 출처.

프론트엔드 `map-webgpu-canvas` 는 `rain` / `coldwave` / `snow` / `chemical` 을 쓰고
GB SafeData 는 `heavy_rain` / `cold_wave` / `heavy_snow` / `chemical_accident` 를 쓴다.
이 매핑을 양쪽에 각각 적어 두면 반드시 갈라진다. 여기 한 곳에만 둔다.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class Hazard(StrEnum):
    HEAVY_RAIN = "heavy_rain"
    FLOOD = "flood"
    LANDSLIDE = "landslide"
    WILDFIRE = "wildfire"
    TYPHOON = "typhoon"
    EARTHQUAKE = "earthquake"
    TSUNAMI = "tsunami"
    HEATWAVE = "heatwave"
    COLD_WAVE = "cold_wave"
    HEAVY_SNOW = "heavy_snow"
    DROUGHT = "drought"
    CHEMICAL_ACCIDENT = "chemical_accident"
    NUCLEAR = "nuclear"


class Readiness(StrEnum):
    READY = "ready"  # 탐지·위험도·대피소 세 축 완비
    PARTIAL = "partial"  # 탐지는 되나 나머지가 빔
    BLOCKED = "blocked"  # 탐지 자체가 없음


# 한국어 이름 · 프론트엔드 맵 시나리오명
_HAZARD_META: dict[Hazard, tuple[str, str | None]] = {
    Hazard.HEAVY_RAIN: ("호우", "rain"),
    Hazard.FLOOD: ("홍수", "flood"),
    Hazard.LANDSLIDE: ("산사태", "landslide"),
    Hazard.WILDFIRE: ("산불", "wildfire"),
    Hazard.TYPHOON: ("태풍", "typhoon"),
    Hazard.EARTHQUAKE: ("지진", "earthquake"),
    Hazard.TSUNAMI: ("지진해일", "tsunami"),
    Hazard.HEATWAVE: ("폭염", "heatwave"),
    Hazard.COLD_WAVE: ("한파", "coldwave"),
    Hazard.HEAVY_SNOW: ("대설", "snow"),
    Hazard.DROUGHT: ("가뭄", "drought"),
    Hazard.CHEMICAL_ACCIDENT: ("화학사고", "chemical"),
    Hazard.NUCLEAR: ("원전", "nuclear"),
}

_BY_MAP_SCENARIO: dict[str, Hazard] = {
    scenario: hazard for hazard, (_, scenario) in _HAZARD_META.items() if scenario
}


def korean_name(hazard: Hazard | str) -> str:
    try:
        return _HAZARD_META[Hazard(hazard)][0]
    except (KeyError, ValueError):
        return str(hazard)


def map_scenario(hazard: Hazard | str) -> str | None:
    try:
        return _HAZARD_META[Hazard(hazard)][1]
    except (KeyError, ValueError):
        return None


def from_map_scenario(scenario: str) -> Hazard | None:
    """프론트엔드 시나리오명을 정규 hazard 값으로. 모르면 None — 추측하지 않는다."""
    if scenario in _BY_MAP_SCENARIO:
        return _BY_MAP_SCENARIO[scenario]
    try:
        return Hazard(scenario)
    except ValueError:
        return None


class HazardAxis(BaseModel):
    label: str
    usable: int = 0
    total: int = 0
    covered: bool = False
    sources: list[str] = []


class HazardCapability(BaseModel):
    """재난 하나가 지금 어디까지 답할 수 있는가.

    `partial` 을 `ready` 처럼 보이게 하면 안 된다. 지진은 발생을 알려주지만
    어느 대피소로 보낼지 모른다. 그래서 `can_detect` 와 `can_say_where_to_go` 가 따로 있다.
    """

    hazard: str
    korean_name: str
    map_scenario: str | None = None
    readiness: Readiness = Readiness.BLOCKED
    can_detect: bool = False
    can_say_where_to_go: bool = False
    axes: dict[str, HazardAxis] = {}
    missing_axes: list[str] = []
    caveat: str | None = None
    connectors: list[str] = []

    @property
    def plan_allowed(self) -> bool:
        """대피 계획을 세워도 되는가 — 갈 곳을 말할 수 없으면 안 된다."""
        return self.can_detect and self.can_say_where_to_go
