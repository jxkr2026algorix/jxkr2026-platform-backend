"""교통수단별 통행 판정.

태그 규칙은 `jxkr2026-datasets/scripts/build_road_network.py` 의 판정을 따른다.
거기 적힌 원칙이 여기서도 그대로다.

> 통행 가능한 길을 빼는 쪽이 막힌 길로 보내는 쪽보다 안전하다.

그래서 모르는 값은 통행 가능으로 두지 않는다. 조건부 제한(`no @ (wet)`)은 조건식을
해석하지 않고 제외한다 — 재난 상황에서 그 조건이 참일 가능성이 높다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class TransportMode(StrEnum):
    FOOT = "foot"
    ASSISTED = "assisted"  # 휠체어·보행보조 — 경사와 노면에 더 엄격하다
    BICYCLE = "bicycle"
    CAR = "car"


# 통행을 막는 접근 태그 값. `emergency` 는 긴급차량 전용이라 주민 자가 대피에 쓸 수 없다.
BLOCKING_ACCESS: frozenset[str] = frozenset(
    {
        "no",
        "private",
        "customers",
        "delivery",
        "agricultural",
        "forestry",
        "emergency",
        "permit",
        "military",
        "destination_only",
    }
)

# 우천 시 통행이 위험하거나 불가한 노면
UNRELIABLE_SURFACE: frozenset[str] = frozenset({"ground", "dirt", "earth", "mud", "sand", "grass"})

IMPASSABLE_SMOOTHNESS: frozenset[str] = frozenset({"impassable", "very_horrible", "horrible"})

FALSY: frozenset[str] = frozenset({"no", "false", "0", ""})

_DRIVABLE = {
    "motorway",
    "motorway_link",
    "trunk",
    "trunk_link",
    "primary",
    "primary_link",
    "secondary",
    "secondary_link",
    "tertiary",
    "tertiary_link",
    "unclassified",
    "residential",
    "living_street",
    "service",
    "track",
}

# 걸어서 갈 수 있는 길. 자동차 전용도로는 뺀다.
_WALKABLE = (_DRIVABLE - {"motorway", "motorway_link", "trunk", "trunk_link"}) | {
    "footway",
    "path",
    "pedestrian",
    "steps",
    "corridor",
}

_CYCLABLE = (_WALKABLE - {"steps", "corridor"}) | {"cycleway"}

# 휠체어·보행보조는 계단과 비포장을 쓸 수 없다.
_ASSISTED = _WALKABLE - {"steps", "path", "track"}


@dataclass(frozen=True)
class ModeProfile:
    """한 교통수단이 어떤 길을 어떤 속도로 지나는가."""

    mode: TransportMode
    grades: frozenset[str]
    # 이 태그들 중 하나라도 차단값이면 통행 불가
    access_tags: tuple[str, ...]
    speed_mps: float
    # 노면·평탄도가 나쁘면 제외하는가
    strict_surface: bool = False
    # 세월교(ford)를 지날 수 있는가. 호우·홍수에서 가장 먼저 끊기는 지점이다.
    allow_ford: bool = False
    korean_name: str = ""
    note: str = ""
    extra_blocked_tags: tuple[str, ...] = field(default_factory=tuple)

    def passable(self, tags: dict[str, str]) -> tuple[bool, str | None]:
        """(통행 가능한가, 불가 사유). 사유를 남기는 이유는 화면이 '왜 못 가는지'를
        말해야 하기 때문이다 — 경로가 없다는 것만으로는 현장에서 판단할 수 없다."""
        highway = str(tags.get("highway", "")).lower().strip()
        if not highway:
            return False, "highway 태그 없음"
        if highway not in self.grades:
            return False, f"{self.korean_name} 통행 등급 아님 ({highway})"

        for tag in self.access_tags:
            value = str(tags.get(tag, "")).lower().strip()
            if value in BLOCKING_ACCESS:
                return False, f"{tag}={value}"

        # 조건부 제한은 조건식을 해석하지 않고 배제한다.
        for tag in (*self.access_tags, "maxweight", "maxheight", "width"):
            if tags.get(f"{tag}:conditional"):
                return False, f"{tag}:conditional (조건 미해석)"

        for tag in self.extra_blocked_tags:
            if str(tags.get(tag, "")).lower().strip() in BLOCKING_ACCESS:
                return False, f"{tag} 차단"

        smoothness = str(tags.get("smoothness", "")).lower().strip()
        if smoothness in IMPASSABLE_SMOOTHNESS:
            return False, f"smoothness={smoothness}"

        if self.strict_surface:
            surface = str(tags.get("surface", "")).lower().strip()
            if surface in UNRELIABLE_SURFACE:
                return False, f"surface={surface}"

        if not self.allow_ford:
            ford = str(tags.get("ford", "")).lower().strip()
            if ford and ford not in FALSY:
                return False, "세월교(ford) — 호우 시 가장 먼저 끊긴다"

        return True, None


PROFILES: dict[TransportMode, ModeProfile] = {
    TransportMode.FOOT: ModeProfile(
        mode=TransportMode.FOOT,
        grades=frozenset(_WALKABLE),
        access_tags=("access", "foot"),
        speed_mps=1.1,  # 야간·우천·고령 인구를 감안한 보수적인 값
        korean_name="도보",
        note="야간·우천을 감안해 4km/h 로 잡는다. 평지 성인 보행속도보다 느리다.",
    ),
    TransportMode.ASSISTED: ModeProfile(
        mode=TransportMode.ASSISTED,
        grades=frozenset(_ASSISTED),
        access_tags=("access", "foot", "wheelchair"),
        speed_mps=0.7,
        strict_surface=True,
        korean_name="보행보조",
        note=(
            "휠체어·보행보조. 계단과 비포장을 제외한다. "
            "이동지원이 필요한 주민은 SGIS 대리지표로 추정할 뿐이므로, "
            "이 경로를 특정 개인에게 배정하는 근거로 쓰면 안 된다."
        ),
    ),
    TransportMode.BICYCLE: ModeProfile(
        mode=TransportMode.BICYCLE,
        grades=frozenset(_CYCLABLE),
        access_tags=("access", "bicycle"),
        speed_mps=3.5,
        korean_name="자전거",
    ),
    TransportMode.CAR: ModeProfile(
        mode=TransportMode.CAR,
        grades=frozenset(_DRIVABLE),
        access_tags=("access", "motor_vehicle", "vehicle", "motorcar"),
        speed_mps=8.3,  # 30km/h — 농로·마을길과 재난 상황을 감안
        korean_name="차량",
        note="마을 진입로와 재난 상황을 감안해 30km/h 로 잡는다.",
    ),
}


def profile_for(mode: TransportMode | str) -> ModeProfile:
    return PROFILES[TransportMode(mode)]
