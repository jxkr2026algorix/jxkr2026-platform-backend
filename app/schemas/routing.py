"""대피 경로 스키마.

경로는 **제안이다.** datasets 레포가 금지 목록에 적어 둔 것 그대로 —
검증되지 않은 경로를 공식 안전경로로 표시하면 안 된다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.routing.profiles import TransportMode

ROUTE_NOTICE = (
    "이 경로는 도로망과 모델 예측으로 계산한 제안입니다. 실시간 통제와 현장 상황이 "
    "모두 반영된 것이 아니며, 공식 안전경로가 아닙니다. 이동 전 담당자가 확인해야 합니다."
)

OSM_ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0"


class RouteRequest(BaseModel):
    community_id: uuid.UUID | None = Field(
        default=None, description="출발 마을. 좌표 대신 이걸 주면 마을 좌표를 쓴다"
    )
    lat: float | None = None
    lon: float | None = None

    hazard: str = Field(description="이 재난에 쓸 수 있는 대피소만 후보로 삼는다")
    mode: TransportMode = TransportMode.FOOT

    shelter_id: uuid.UUID | None = Field(
        default=None, description="지정하면 그 대피소만. 없으면 후보를 비교한다"
    )
    max_shelters: int = Field(default=3, ge=1, le=10)

    incident_id: uuid.UUID | None = Field(
        default=None,
        description="주면 그 상황의 현장 보고에서 확인된 통제 구간을 반영한다",
    )

    use_prediction: bool = Field(
        default=True, description="자체 모델 예측으로 시간에 따른 확산을 반영한다"
    )
    horizons_minutes: list[int] = Field(
        default_factory=lambda: [30, 60, 120],
        description="예측을 받을 시점들. 대피에 걸리는 시간만큼은 덮어야 한다",
    )
    block_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    avoid_threshold: float = Field(default=0.2, ge=0.0, le=1.0)
    depart_after_minutes: float = Field(
        default=0.0, ge=0.0, description="출발까지 걸리는 시간. 그만큼 위험이 더 커진다"
    )


class BlockedSegment(BaseModel):
    kind: str
    lat: float
    lon: float
    radius_m: float
    detail: str | None = None


class RouteLeg(BaseModel):
    """대피소 하나로 가는 경로 하나."""

    shelter_id: uuid.UUID
    shelter_name: str
    shelter_capacity: int | None = None
    capacity_basis: str | None = None

    found: bool
    reason: str | None = Field(default=None, description="못 찾았을 때 사유. 빈 경로와 구분된다")
    geometry: list[list[float]] = Field(
        default_factory=list, description="GeoJSON LineString 좌표 [[lon, lat], ...]"
    )
    distance_m: float | None = None
    duration_minutes: float | None = None
    straight_line_km: float | None = None
    max_risk: float | None = Field(
        default=None, description="경로상 최대 위험. 지나는 시각 기준으로 잰다"
    )
    mean_risk: float | None = None
    avoided_edges: int = 0
    blocked_by_reports: list[BlockedSegment] = Field(default_factory=list)


class RoutePlan(BaseModel):
    origin: dict = Field(description="{lat, lon, community_id?, community_name?}")
    hazard: str
    mode: TransportMode
    mode_name: str
    mode_note: str | None = None

    routes: list[RouteLeg] = Field(description="도착 시간 순. found=false 도 사유와 함께 남는다")
    recommended: uuid.UUID | None = Field(
        default=None, description="가장 빠른 경로의 대피소. 없으면 null"
    )

    # 이 재난에 대해 '어디로 가라'를 말할 수 있는가.
    # 지진은 발생을 알려주지만 갈 곳을 모른다 — 그건 계산 실패가 아니라 알려진 한계다.
    shelter_guidance_available: bool = True
    hazard_limitation: str | None = Field(
        default=None,
        description="대피소를 안내할 수 없는 재난일 때 그 사유. 화면에 그대로 띄운다",
    )

    prediction_used: bool = False
    prediction_id: str | None = None
    prediction_model: str | None = None
    feature_mode: str | None = None
    prediction_is_stub: bool = False
    horizons_minutes: list[int] = Field(default_factory=list)

    field_reports_applied: int = 0
    road_network: str | None = None
    attribution: str = OSM_ATTRIBUTION
    is_derived: bool = True
    notice: str = ROUTE_NOTICE
    warnings: list[str] = Field(default_factory=list)
    generated_at: datetime


class ModeInfo(BaseModel):
    mode: TransportMode
    korean_name: str
    speed_kmh: float
    note: str | None = None
    grades: list[str]


class ModeCatalog(BaseModel):
    modes: list[ModeInfo]
    road_network_loaded: bool
    road_network_source: str | None = None
    attribution: str = OSM_ATTRIBUTION
