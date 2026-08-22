"""ML 추론 서버 클라이언트.

실제 서버는 `jxkr2026-mlengine/serving` 이다 — Lambda H100 위에서 Triton Inference Server
앞에 FastAPI 게이트웨이가 붙어 있고, 이 백엔드(EC2)는 그 URL 을 docker compose 에서 받는다.

    SALGIL_MLENGINE_MODE=http
    SALGIL_MLENGINE_BASE_URL=http://ml.internal:8900
    SALGIL_MLENGINE_API_KEY=<compose 에서 주입>

`stub` 모드는 ML 서버 없이도 프론트엔드가 붙어 돌아가게 하기 위한 것이다. 스텁 응답은
**결정론적**이고 `is_stub=true` 를 달고 나간다. 화면에서 진짜 예측처럼 보이면 안 된다.
"""

from __future__ import annotations

import hashlib
import math
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import UpstreamError, UpstreamTimeout, ValidationError
from app.core.logging import get_logger
from app.schemas.prediction import (
    FeatureMode,
    GridSpec,
    ModelCard,
    ModelCatalog,
    ModelRef,
    PredictionRequest,
    PredictionResult,
    PredictionSummary,
    Recipe,
    TensorPayload,
)

log = get_logger(__name__)

UPSTREAM = "mlengine"

STUB_NOTICE = (
    "ML 추론 서버가 stub 모드입니다 — 학습된 모델이 아니라 결정론적 합성값입니다. "
    "운영 판단에 쓰면 안 됩니다."
)

# 재난 유형과 recipe 의 대응. 화면이 재난만 알 때 어떤 모델을 부를지 정한다.
RECIPE_FOR_HAZARD: dict[str, Recipe] = {
    "heavy_rain": Recipe.RAIN_NOWCAST,
    "flood": Recipe.FLOOD_EXTENT,
    "landslide": Recipe.LANDSLIDE_RISK,
    # 산불은 '이미 난 불의 확산'과 '발생 확률'이 다른 모델이다. 기본은 확산이고,
    # 발생 예측이 필요하면 recipe 를 직접 지정한다.
    "wildfire": Recipe.WILDFIRE_SPREAD,
    "typhoon": Recipe.TYPHOON_TRACK_INTENSITY,
    "heatwave": Recipe.WEATHER_EXTREMES,
    "cold_wave": Recipe.WEATHER_EXTREMES,
}


class MlEngineClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._mode = settings.mlengine_mode
        self._owns_client = client is None
        self._client: httpx.AsyncClient | None = None
        if self._mode == "http":
            headers = {
                "accept": "application/json",
                "user-agent": "salgil-platform-backend/0.1 (+jxkr2026)",
            }
            if settings.mlengine_api_key:
                headers["authorization"] = f"Bearer {settings.mlengine_api_key}"
            self._client = client or httpx.AsyncClient(
                base_url=settings.mlengine_base_url.rstrip("/"),
                timeout=httpx.Timeout(settings.mlengine_timeout_s, connect=10.0),
                headers=headers,
            )

    @property
    def mode(self) -> str:
        return self._mode

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()

    # ── 공개 ──────────────────────────────────────────────────────────────────

    async def catalog(self) -> ModelCatalog:
        if self._mode == "stub":
            return ModelCatalog(
                models=[
                    ModelCard(
                        recipe=recipe,
                        model=ModelRef(name=recipe.value, version="stub", backend="stub"),
                        ready=True,
                        hazards=[h for h, r in RECIPE_FOR_HAZARD.items() if r is recipe],
                        detail=STUB_NOTICE,
                    )
                    for recipe in Recipe
                ],
                served_by="stub",
                fetched_at=datetime.now(UTC),
            )
        payload = await self._request("GET", "/v1/models")
        return ModelCatalog.model_validate(payload)

    async def predict(self, request: PredictionRequest) -> PredictionResult:
        if self._mode == "stub":
            return _stub_prediction(request)
        payload = await self._request(
            "POST",
            f"/v1/predict/{request.recipe.value}",
            json=request.model_dump(mode="json", exclude_none=True),
        )
        return PredictionResult.model_validate(payload)

    async def ping(self) -> tuple[bool, str | None, float | None]:
        if self._mode == "stub":
            return True, "stub 모드 — 실제 추론 서버에 붙어 있지 않습니다", None
        started = time.perf_counter()
        try:
            # /readyz 가 아니라 /v1/ping 을 부른다. /readyz 는 인증이 없어서,
            # 토큰을 compose 에 넣는 것을 잊어도 '준비됨'으로 보고된다 —
            # 그러면 첫 추론 요청에서야 403 을 만난다.
            payload = await self._request("GET", "/v1/ping", timeout=5.0)
        except UpstreamError as exc:
            return False, exc.detail, None
        except ValidationError as exc:
            return False, exc.detail, None
        detail = None
        if isinstance(payload, dict) and payload.get("triton_ready") is False:
            detail = "게이트웨이는 살아 있지만 Triton 이 준비되지 않았습니다"
            return False, detail, round((time.perf_counter() - started) * 1000, 1)
        return True, detail, round((time.perf_counter() - started) * 1000, 1)

    # ── 내부 ──────────────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        timeout: float | None = None,
    ) -> Any:
        if self._client is None:
            raise UpstreamError("ML 추론 서버가 설정되지 않았습니다", upstream=UPSTREAM)
        try:
            response = await self._client.request(method, path, json=json, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise UpstreamTimeout(
                f"ML 추론 서버 {path} 응답이 시간 안에 오지 않았습니다", upstream=UPSTREAM
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"ML 추론 서버 {path} 요청 실패: {exc}", upstream=UPSTREAM) from exc

        if response.status_code in (401, 403):
            raise UpstreamError(
                "ML 추론 서버 인증에 실패했습니다 — SALGIL_MLENGINE_API_KEY 를 확인하세요",
                upstream=UPSTREAM,
                upstream_status=response.status_code,
            )
        if response.status_code in (400, 404, 422):
            # 상류가 4xx 를 준 것은 **우리 요청이 틀렸다**는 뜻이다. 502 로 올리면
            # 호출자에게 "ML 서버가 죽었다"로 읽혀 엉뚱한 곳을 보게 된다.
            raise ValidationError(
                _upstream_detail(response) or "ML 추론 서버가 요청을 거절했습니다",
                upstream=UPSTREAM,
                upstream_status=response.status_code,
            )
        if response.status_code >= 400:
            raise UpstreamError(
                f"ML 추론 서버가 HTTP {response.status_code} 로 응답했습니다: "
                f"{response.text[:300]}",
                upstream=UPSTREAM,
                upstream_status=response.status_code,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError(
                f"ML 추론 서버 응답이 JSON 이 아닙니다: {response.text[:200]}", upstream=UPSTREAM
            ) from exc


def _upstream_detail(response: httpx.Response) -> str | None:
    """상류의 problem+json 에서 사람이 읽을 사유만 꺼낸다."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:300] or None
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("title")
        if isinstance(detail, str):
            return detail
    return None


# ── 스텁 ──────────────────────────────────────────────────────────────────────


def _seed(request: PredictionRequest) -> int:
    raw = f"{request.recipe.value}|{request.region_code}|{request.horizon_minutes}"
    return int(hashlib.sha256(raw.encode()).hexdigest()[:8], 16)


def _stub_prediction(request: PredictionRequest) -> PredictionResult:
    """결정론적 합성 격자.

    같은 입력이면 같은 값이 나온다. 테스트가 흔들리지 않고, 시연에서 값이 튀지 않는다.
    """
    started = time.perf_counter()
    grid = request.grid or GridSpec(height=32, width=32, cell_size_m=100.0, crs="EPSG:5179")
    seed = _seed(request)
    threshold = request.threshold if request.threshold is not None else 0.5

    values: list[float] = []
    cy, cx = grid.height / 2.0, grid.width / 2.0
    for row in range(grid.height):
        for col in range(grid.width):
            # 중심에서 멀어질수록 낮아지는 매끄러운 언덕 + 씨앗 기반 위상차
            dist = math.hypot((row - cy) / max(cy, 1.0), (col - cx) / max(cx, 1.0))
            phase = ((seed >> ((row + col) % 16)) & 0xFF) / 255.0
            value = max(0.0, min(1.0, math.exp(-1.8 * dist * dist) * (0.55 + 0.45 * phase)))
            values.append(round(value, 5))

    ordered = sorted(values, reverse=True)
    over = sum(1 for v in values if v >= threshold)
    top_cells = []
    for flat_index in sorted(range(len(values)), key=lambda i: values[i], reverse=True)[:5]:
        top_cells.append(
            {
                "row": flat_index // grid.width,
                "col": flat_index % grid.width,
                "value": values[flat_index],
            }
        )

    summary = PredictionSummary(
        max=ordered[0] if ordered else None,
        mean=round(sum(values) / len(values), 5) if values else None,
        p95=ordered[max(0, int(len(ordered) * 0.05) - 1)] if ordered else None,
        threshold=threshold,
        cells_over_threshold=over,
        total_cells=len(values),
        top_cells=top_cells,
    )

    return PredictionResult(
        prediction_id=str(uuid.uuid4()),
        recipe=request.recipe,
        status="succeeded",
        model=ModelRef(name=request.recipe.value, version="stub", backend="stub"),
        region_code=request.region_code,
        hazard=request.hazard,
        as_of=request.as_of or datetime.now(UTC),
        horizon_minutes=request.horizon_minutes,
        grid=grid,
        outputs=[
            TensorPayload(
                name="risk",
                dtype="float32",
                shape=[grid.height, grid.width],
                data=values,
            )
        ],
        summary=summary,
        feature_mode=FeatureMode.SYNTHETIC,
        feature_sources=[],
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
        is_stub=True,
        warnings=[STUB_NOTICE],
        generated_at=datetime.now(UTC),
    )
