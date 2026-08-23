"""자체 예측 모델 계약 — 플랫폼 백엔드 ↔ ML 추론 서버.

**이 계약은 두 레포가 공유한다.** 여기 스키마와 `jxkr2026-mlengine/serving` 의 스키마가
갈라지면 조용히 틀린 값이 흐른다. 필드를 바꾸면 양쪽을 같이 바꾼다.

산출값은 어느 기관도 보증하지 않는 **자체 모델 값**이다. 산림청 위험등급 1~5 같은 공식
값과 같은 화면에 놓이므로, 응답마다 그 사실을 싣는다.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

DERIVED_NOTICE = (
    "자체 모델이 만든 파생 지표입니다. 어느 기관도 보증하지 않으며, "
    "공식 위험등급·특보와 같은 값으로 취급하면 안 됩니다."
)


class Recipe(StrEnum):
    """jxkr2026-mlengine 의 recipe registry 와 이름이 같아야 한다."""

    FLOOD_EXTENT = "flood_extent"
    STURM_FLOOD_EXTENT = "sturm_flood_extent"
    LANDSLIDE_RISK = "landslide_risk"
    RAIN_NOWCAST = "rain_nowcast"
    RIVER_GRAPH = "river_graph"
    TYPHOON_TRACK_INTENSITY = "typhoon_track_intensity"
    WEATHER_EXTREMES = "weather_extremes"
    WILDFIRE_SPREAD = "wildfire_spread"
    KOREA_WILDFIRE_IGNITION = "korea_wildfire_ignition"


class FeatureMode(StrEnum):
    REAL = "real"  # 실제 관측에서 조립한 입력
    SYNTHETIC = "synthetic"  # 결정론적 합성 입력 — 시연·스모크용
    PROVIDED = "provided"  # 호출자가 텐서를 직접 넣음


class GridSpec(BaseModel):
    height: int
    width: int
    cell_size_m: float | None = None
    crs: str | None = Field(default=None, description="예: EPSG:4326")
    bbox: list[float] | None = Field(
        default=None,
        description=(
            "[minx, miny, maxx, maxy] — crs 기준. **경로 계산에 쓰려면 필수다.** "
            "없으면 백엔드가 격자 위치를 가정할 수밖에 없고, 가정이 틀리면 위험 구역이 "
            "실제와 다른 자리에 놓인다"
        ),
    )


class TensorPayload(BaseModel):
    """Triton 텐서 하나. data 는 row-major 로 편 1차원 배열이다."""

    model_config = ConfigDict(protected_namespaces=())

    name: str
    dtype: str = "float32"
    shape: list[int]
    data: list[float]


class PredictionRequest(BaseModel):
    """추론 요청.

    입력을 주는 방법은 둘 중 하나다.
    - `inputs` 를 직접 넣는다 (결정론적, 테스트에 쓴다)
    - `region_code` 만 주고 서버가 관측에서 조립하게 한다 (`feature_mode` 로 무엇을 썼는지 알려준다)
    """

    model_config = ConfigDict(protected_namespaces=())

    recipe: Recipe
    region_code: str | None = Field(default=None, description="경북 시군 코드 (예: 47280)")
    hazard: str | None = None
    as_of: datetime | None = Field(default=None, description="이 시각 기준으로 조립. 없으면 현재")
    horizon_minutes: int | None = Field(default=None, ge=0, le=72 * 60)
    grid: GridSpec | None = None
    # 격자가 덮어야 할 지도상 범위. **형상은 모델이 정하고 범위는 호출자가 정한다** —
    # 호출자는 모델의 격자 크기를 미리 모르므로 grid 로는 범위를 줄 수 없다.
    #
    # 주지 않으면 응답에도 범위가 없고, 받는 쪽은 격자가 어디에 놓이는지 추측하게 된다.
    # 추측이 틀리면 위험 구역이 실제와 다른 자리에 그려지고 경로가 엉뚱한 길을 피한다.
    bbox: list[float] | None = Field(
        default=None, description="[minlon, minlat, maxlon, maxlat] — WGS84"
    )
    crs: str | None = Field(default=None, description="bbox 의 좌표계. 기본 EPSG:4326")
    inputs: list[TensorPayload] | None = None
    model_version: str | None = Field(default=None, description="없으면 서버 기본 버전")
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    incident_id: str | None = None


class ChannelSummary(BaseModel):
    """출력 채널 하나 — 재난 하나.

    한 모델이 여러 재난을 한 텐서에 담는다 (`weather_extremes` 는 폭염·한파·대설·가뭄).
    전체 요약만 보면 "64셀이 임계를 넘었다"까지만 알 수 있고 **어느 재난인지 모른다.**
    화면이 가뭄 경고와 폭염 경고를 구분하려면 이 배열을 읽어야 한다.
    """

    channel: int
    hazard: str | None = None
    max: float | None = None
    mean: float | None = None
    p95: float | None = None
    cells_over_threshold: int | None = None


class PredictionSummary(BaseModel):
    """화면이 실제로 쓰는 요약. 격자 원본은 ML 서버가 소유한다."""

    max: float | None = None
    mean: float | None = None
    p95: float | None = None
    threshold: float | None = None
    cells_over_threshold: int | None = None
    total_cells: int | None = None
    top_cells: list[dict] = Field(
        default_factory=list, description="[{row, col, value, lat?, lon?}] 상위 셀"
    )
    channels: list[ChannelSummary] = Field(
        default_factory=list,
        description=(
            "채널이 여럿일 때 재난별 요약. 전체 요약은 채널 축 최댓값이라 "
            "어느 재난이 위험한지 말해 주지 않는다"
        ),
    )


class ModelRef(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    name: str
    version: str | None = None
    backend: str | None = Field(default=None, description="triton | stub")
    framework: str | None = None
    checkpoint: str | None = None
    trained_at: datetime | None = None


class PredictionResult(BaseModel):
    """추론 결과. `is_derived` 와 `derived_notice` 는 항상 실린다."""

    model_config = ConfigDict(protected_namespaces=())

    prediction_id: str
    recipe: Recipe
    status: str = "succeeded"
    model: ModelRef
    region_code: str | None = None
    hazard: str | None = None
    as_of: datetime | None = None
    horizon_minutes: int | None = None
    grid: GridSpec | None = None
    outputs: list[TensorPayload] = Field(
        default_factory=list, description="원본 텐서. 격자가 크면 생략될 수 있다"
    )
    summary: PredictionSummary = Field(default_factory=PredictionSummary)
    feature_mode: FeatureMode = FeatureMode.SYNTHETIC
    feature_sources: list[str] = Field(default_factory=list)
    latency_ms: float | None = None
    is_stub: bool = Field(default=False, description="ML 서버가 스텁 모드로 답했다")
    is_derived: bool = True
    derived_notice: str = DERIVED_NOTICE
    warnings: list[str] = Field(default_factory=list)
    generated_at: datetime | None = None


class ModelCard(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    recipe: Recipe
    model: ModelRef
    ready: bool
    inputs: list[dict] = []
    outputs: list[dict] = []
    hazards: list[str] = []
    detail: str | None = None


class ModelCatalog(BaseModel):
    models: list[ModelCard]
    served_by: str
    fetched_at: datetime | None = None
