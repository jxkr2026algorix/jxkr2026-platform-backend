"""시간에 따라 커지는 위험 구역.

산불은 퍼진다. "지금 안전한 길"이 30분 뒤에는 아닐 수 있고, 대피에 40분이 걸리는
경로라면 40분 뒤의 상태로 판단해야 한다. 그래서 예측을 여러 시점(horizon)으로 받아
시간축을 가진 위험장을 만든다.

**시간에 대해 단조증가하도록 만든다.** 한 번 위험해진 칸은 계속 위험한 것으로 둔다.
두 가지 이유가 있다.

- 물리적으로 보수적이다. 불이 지나간 자리를 안전하다고 보고 경로를 내면 안 된다.
- 탐색이 성립한다. 도착이 이를수록 손해가 아니어야(FIFO) 다익스트라가 최적을 준다.
  단조성을 가정하지 않고 **구성으로 보장**한다.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

from app.routing.graph import haversine_m


@dataclass(frozen=True)
class HazardSlice:
    """한 시점의 위험 격자.

    격자는 [minlat, minlon, maxlat, maxlon] 범위에 행×열로 놓인다. 행 0 이 북쪽이다
    (래스터 관행). 예측 응답의 격자를 그대로 받는다.
    """

    horizon_minutes: int
    height: int
    width: int
    bbox: tuple[float, float, float, float]
    values: list[float]

    def value_at(self, lat: float, lon: float) -> float | None:
        min_lat, min_lon, max_lat, max_lon = self.bbox
        if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
            return None
        lat_span = max(max_lat - min_lat, 1e-9)
        lon_span = max(max_lon - min_lon, 1e-9)
        row = int((max_lat - lat) / lat_span * self.height)
        col = int((lon - min_lon) / lon_span * self.width)
        row = min(max(row, 0), self.height - 1)
        col = min(max(col, 0), self.width - 1)
        return self.values[row * self.width + col]


@dataclass(frozen=True)
class BlockedPoint:
    """현장이 확인한 통제 지점.

    예측보다 강하다 — 사람이 가서 본 것이다. 확률이 아니라 차단이다.
    """

    lat: float
    lon: float
    radius_m: float
    kind: str
    detail: str | None = None


@dataclass
class HazardField:
    """시간축을 가진 위험장 + 현장 통제 지점."""

    slices: list[HazardSlice] = field(default_factory=list)
    blocked: list[BlockedPoint] = field(default_factory=list)
    hazard: str | None = None
    model: str | None = None
    feature_mode: str | None = None
    is_stub: bool = False

    def __post_init__(self) -> None:
        self.slices = sorted(self.slices, key=lambda s: s.horizon_minutes)
        self._horizons = [s.horizon_minutes for s in self.slices]

    @property
    def horizons(self) -> list[int]:
        return list(self._horizons)

    @property
    def has_prediction(self) -> bool:
        return bool(self.slices)

    def blocked_by_field_report(self, lat: float, lon: float) -> BlockedPoint | None:
        for point in self.blocked:
            if haversine_m(lat, lon, point.lat, point.lon) <= point.radius_m:
                return point
        return None

    def risk_at(self, lat: float, lon: float, elapsed_s: float) -> float:
        """`elapsed_s` 초 뒤에 이 지점이 얼마나 위험한가.

        해당 시점까지의 모든 시점 중 최댓값을 쓴다 — 단조증가를 구성으로 보장한다.
        예측 범위를 벗어난 시각은 마지막 시점의 값으로 둔다. 예측이 끝났다고
        위험이 끝난 것은 아니므로 0 으로 떨어뜨리지 않는다.
        """
        if not self.slices:
            return 0.0
        minutes = elapsed_s / 60.0
        index = bisect.bisect_right(self._horizons, minutes)
        if index == 0:
            # 가장 이른 시점보다도 전이면 그 시점 값을 쓴다. 예측이 없는 구간을
            # 안전으로 읽으면 출발 직후 구간이 무조건 통과된다.
            index = 1
        worst = 0.0
        for hazard_slice in self.slices[:index]:
            value = hazard_slice.value_at(lat, lon)
            if value is not None:
                worst = max(worst, value)
        return worst


@dataclass(frozen=True)
class HazardPolicy:
    """위험을 비용과 차단으로 옮기는 규칙."""

    block_threshold: float = 0.5
    avoid_threshold: float = 0.2
    # 회피 구간을 지날 때 시간이 몇 배로 비싸지는가. 우회로가 있으면 돌아가게 만든다.
    avoid_penalty: float = 8.0

    def edge_multiplier(self, risk: float) -> float | None:
        """None 이면 통행 불가."""
        if risk >= self.block_threshold:
            return None
        if risk <= self.avoid_threshold:
            return 1.0
        span = max(self.block_threshold - self.avoid_threshold, 1e-9)
        ratio = (risk - self.avoid_threshold) / span
        return 1.0 + ratio * (self.avoid_penalty - 1.0)


def to_probability(tensor_name: str, values: list[float]) -> list[float]:
    """모델 출력을 0..1 확률로 맞춘다.

    **내보낸 그래프는 로짓을 낸다.** `jxkr-export-onnx` 가 로짓 헤드를 `logits` /
    `*_logits` 로 이름 짓고, 게이트웨이의 `summary` 는 시그모이드를 씌우지만
    `outputs` 의 원본 텐서는 로짓 그대로다.

    로짓을 확률로 오인하면 임계값의 뜻이 통째로 달라진다. 로짓 3.0 은 확률 0.95 인데
    임계 0.8 과 비교하면 차단이고, 임계를 0.99 로 올려도 여전히 차단이라 **모든 길이
    막힌 것처럼 보인다.** 실제로 그렇게 나갔다.

    이름으로 판단하는 것은 게이트웨이의 `_is_logits` 와 같은 규칙이다. 사람이 유지하는
    플래그를 따로 두면 계약이 바뀔 때 같이 바뀌지 않는다.
    """
    if not (tensor_name == "logits" or tensor_name.endswith("_logits")):
        return list(values)
    return [_sigmoid(value) for value in values]


def _sigmoid(value: float) -> float:
    if value > 50:
        return 1.0
    if value < -50:
        return 0.0
    return 1.0 / (1.0 + math.exp(-value))


def slice_from_grid(
    *,
    horizon_minutes: int,
    height: int,
    width: int,
    bbox: tuple[float, float, float, float],
    values: list[float],
    channel: int | None = None,
    channel_count: int = 1,
) -> HazardSlice:
    """예측 텐서를 격자 하나로 만든다.

    채널이 여럿이면 하나를 골라야 한다. 채널 축을 최댓값으로 접으면 폭염 위험이
    산불 경로 계산에 섞인다.
    """
    if channel_count > 1 and channel is not None:
        values = values[channel::channel_count]
    expected = height * width
    if len(values) != expected:
        raise ValueError(
            f"격자 {height}×{width} 는 원소 {expected}개가 필요합니다 (받은 값 {len(values)}개)"
        )
    return HazardSlice(
        horizon_minutes=horizon_minutes,
        height=height,
        width=width,
        bbox=bbox,
        values=[float(v) for v in values],
    )


def bbox_around(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    """중심과 반경으로 대략적인 위경도 범위를 만든다."""
    lat_delta = radius_m / 111_320.0
    lon_delta = radius_m / (111_320.0 * max(math.cos(math.radians(lat)), 1e-6))
    return (lat - lat_delta, lon - lon_delta, lat + lat_delta, lon + lon_delta)
