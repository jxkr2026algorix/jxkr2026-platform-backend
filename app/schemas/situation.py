"""상황판 스키마 — 관측 봉투 + 가용성 + 운영 상태를 한 화면 분으로 묶는다."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import DataState, Envelope
from app.schemas.hazard import HazardCapability
from app.schemas.meta import ResolvedRegion


class SituationContext(BaseModel):
    """특정 지역·재난의 현재 상황.

    봉투를 줄이지 않고 그대로 싣는다. `capability` 를 같이 주는 이유는,
    `records` 가 비었을 때 그게 '없음'인지 '애초에 못 보는 것'인지 화면이 구분해야 하기 때문이다.
    """

    region: ResolvedRegion
    hazard: str | None = None
    hazard_korean: str | None = None
    capability: HazardCapability | None = None
    envelope: Envelope
    state: DataState
    headline_caveat: str | None = Field(
        default=None, description="화면 상단에 그대로 띄울 한 줄. 없으면 null"
    )
    fetched_at: datetime


class WeatherReading(BaseModel):
    """관측값 하나. 값만 주지 않고 언제·어디서 온 것인지 함께 준다."""

    kind: str = Field(description="temperature, humidity, wind_speed, rainfall_1h …")
    value: float | None = None
    unit: str | None = None
    station: str | None = None
    observed_at: datetime | None = None
    is_forecast: bool = False
    # 갱신주기를 넘긴 값인가. 화면은 이 값을 숨기지 말고 시각과 함께 보여야 한다.
    stale: bool = False


class WeatherSnapshot(BaseModel):
    """시군 하나의 현재 기상.

    `situation/context` 의 봉투를 화면이 쓰기 좋은 모양으로 추린 것이다. 봉투 자체가
    필요하면 `/situation/context` 를 쓴다 — 여기서도 `state` 와 출처는 유지한다.

    **하드코딩된 데모 값을 대체하려고 만들었다.** 값이 없으면 빈 값을 지어내지 않고
    `state=UNVERIFIED` 로 답한다 — 화면이 '없음'과 '못 읽음'을 구분해야 한다.
    """

    region: ResolvedRegion
    state: DataState
    readings: list[WeatherReading] = []
    # 자주 쓰는 값을 꺼내 둔다. 없으면 null 이고, 0 으로 채우지 않는다.
    temperature_c: float | None = None
    humidity_pct: float | None = None
    wind_speed_ms: float | None = None
    wind_direction_deg: float | None = None
    rainfall_1h_mm: float | None = None
    observed_at: datetime | None = None
    stale: bool = Field(
        default=False, description="갱신주기를 넘긴 값이 섞여 있다 — 관측 시각을 함께 표시"
    )
    caveats: list[str] = []
    attribution: str | None = Field(
        default=None, description="출처 표기 문구. 화면에서 지우면 안 된다"
    )
    source_url: str | None = None
    fetched_at: datetime


class SourceProbe(BaseModel):
    """원천 하나 직접 조회."""

    connector: str
    region: str | None = None
    envelope: Envelope
    state: DataState
    fetched_at: datetime


class SituationOverview(BaseModel):
    """콘솔 첫 화면 한 번에 필요한 것.

    프론트엔드가 여러 번 왕복하지 않도록 묶어 준다. 묶더라도 각 조각의 봉투는 유지한다.
    """

    region: ResolvedRegion
    generated_at: datetime
    hazards: list[HazardSnapshot] = []
    open_incidents: int = 0
    unverified_sources: list[str] = Field(
        default_factory=list, description="지금 읽지 못하는 원천 — 화면에 사유를 띄운다"
    )


class HazardSnapshot(BaseModel):
    hazard: str
    hazard_korean: str
    map_scenario: str | None = None
    readiness: str
    state: DataState
    record_count: int = 0
    complete: bool = False
    absence_confirmed: bool = False
    caveat: str | None = None
    failed_sources: list[str] = []


SituationOverview.model_rebuild()
