"""자체 예측 모델 — ML 추론 서버(Triton) 호출.

응답에는 항상 `is_derived=true` 와 안내 문구가 실린다. 공식 위험등급과 같은 화면에
놓이기 때문이다. 화면에서 이 구분을 지우면 안 된다.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, status

from app.api.deps import Db, MlEngine, RequireOperator, actor_name
from app.api.route import TransactionalRoute
from app.core.errors import NotFoundError
from app.db.models import PredictionRun
from app.schemas.prediction import ModelCatalog, PredictionRequest, PredictionResult
from app.services import predictions

router = APIRouter(prefix="/predictions", tags=["predictions"], route_class=TransactionalRoute)


@router.get(
    "/models",
    response_model=ModelCatalog,
    summary="추론 가능한 모델 목록",
    description="ML 서버가 stub 모드면 `served_by=stub` 이고 값은 합성이다.",
)
async def list_models(client: MlEngine, _: RequireOperator) -> ModelCatalog:
    return await client.catalog()


@router.post(
    "",
    response_model=PredictionResult,
    status_code=status.HTTP_201_CREATED,
    summary="추론 실행",
    description=(
        "**이 응답은 자체 모델 산출값이다.** 산림청 산사태위험등급 1~5 같은 공식 값이 아니다. "
        "`is_derived` 와 `derived_notice` 를 화면에 유지해야 한다."
    ),
)
async def create_prediction(
    payload: PredictionRequest,
    session: Db,
    client: MlEngine,
    current: RequireOperator,
    request: Request,
) -> PredictionResult:
    return await predictions.run(session, client, payload, actor=actor_name(request, current))


@router.get("/runs", summary="예측 실행 이력")
async def list_runs(
    session: Db,
    _: RequireOperator,
    incident_id: uuid.UUID | None = Query(default=None),
    recipe: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
) -> list[dict]:
    rows = await predictions.history(session, incident_id=incident_id, recipe=recipe, limit=limit)
    return [
        {
            "id": str(row.id),
            "recipe": row.recipe,
            "region_code": row.region_code,
            "hazard": row.hazard,
            "status": row.status,
            "model_name": row.model_name,
            "model_version": row.model_version,
            "served_by": row.served_by,
            "feature_mode": row.feature_mode,
            "is_stub": row.is_stub,
            "requested_at": row.requested_at,
            "latency_ms": row.latency_ms,
            "summary": row.summary,
            "error_detail": row.error_detail,
        }
        for row in rows
    ]


@router.get("/runs/{run_id}", summary="예측 실행 하나")
async def get_run(run_id: uuid.UUID, session: Db, _: RequireOperator) -> dict:
    row = await session.get(PredictionRun, run_id)
    if row is None:
        raise NotFoundError(f"예측 실행 {run_id} 를 찾지 못했습니다")
    return {
        "id": str(row.id),
        "recipe": row.recipe,
        "region_code": row.region_code,
        "hazard": row.hazard,
        "status": row.status,
        "model_name": row.model_name,
        "model_version": row.model_version,
        "served_by": row.served_by,
        "feature_mode": row.feature_mode,
        "is_stub": row.is_stub,
        "requested_at": row.requested_at,
        "latency_ms": row.latency_ms,
        "summary": row.summary,
        "request_payload": row.request_payload,
        "error_detail": row.error_detail,
    }
