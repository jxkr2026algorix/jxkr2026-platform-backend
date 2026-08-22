"""예측 서비스 — ML 추론 서버 호출과 그 이력 보관.

실행 결과를 DB 에 남기는 이유는 두 가지다.
- 계획의 근거로 예측 id 를 인용할 수 있어야 한다
- 나중에 "그때 어떤 모델의 어떤 버전을 봤는가"에 답할 수 있어야 한다

격자 원본은 남기지 않는다. 그건 ML 서버가 소유한다. 여기 남는 것은 요약이다.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.mlengine import RECIPE_FOR_HAZARD, MlEngineClient
from app.core.errors import UpstreamError, ValidationError
from app.db.base import utcnow
from app.db.models import PredictionRun
from app.schemas.prediction import PredictionRequest, PredictionResult, Recipe


def recipe_for_hazard(hazard: str) -> Recipe:
    recipe = RECIPE_FOR_HAZARD.get(hazard)
    if recipe is None:
        raise ValidationError(
            f"재난 '{hazard}' 에 대응하는 예측 모델이 없습니다 — 지원: {sorted(RECIPE_FOR_HAZARD)}"
        )
    return recipe


async def run(
    session: AsyncSession,
    client: MlEngineClient,
    request: PredictionRequest,
    *,
    actor: str,
) -> PredictionResult:
    started = utcnow()
    incident_uuid = uuid.UUID(request.incident_id) if request.incident_id else None

    try:
        result = await client.predict(request)
    except UpstreamError as exc:
        # 실패도 이력이다. 남기지 않으면 "그때 모델이 죽어 있었다"를 나중에 알 수 없다.
        session.add(
            PredictionRun(
                incident_id=incident_uuid,
                recipe=request.recipe.value,
                region_code=request.region_code or "",
                hazard=request.hazard,
                horizon_minutes=request.horizon_minutes,
                status="failed",
                served_by=client.mode,
                requested_at=started,
                error_detail=exc.detail[:2000],
                request_payload=request.model_dump(mode="json", exclude={"inputs"}),
            )
        )
        await session.flush()
        raise

    session.add(
        PredictionRun(
            id=uuid.UUID(result.prediction_id) if _is_uuid(result.prediction_id) else uuid.uuid4(),
            incident_id=incident_uuid,
            recipe=result.recipe.value,
            region_code=result.region_code or request.region_code or "",
            hazard=result.hazard or request.hazard,
            horizon_minutes=result.horizon_minutes,
            status=result.status,
            model_name=result.model.name,
            model_version=result.model.version,
            served_by=result.model.backend or client.mode,
            feature_mode=result.feature_mode.value,
            is_stub=result.is_stub,
            requested_at=started,
            latency_ms=result.latency_ms,
            summary=result.summary.model_dump(mode="json"),
            request_payload=request.model_dump(mode="json", exclude={"inputs"}),
        )
    )
    await session.flush()
    return result


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


async def history(
    session: AsyncSession,
    *,
    incident_id: uuid.UUID | None = None,
    recipe: str | None = None,
    limit: int = 50,
) -> list[PredictionRun]:
    stmt = select(PredictionRun).order_by(PredictionRun.requested_at.desc()).limit(limit)
    if incident_id:
        stmt = stmt.where(PredictionRun.incident_id == incident_id)
    if recipe:
        stmt = stmt.where(PredictionRun.recipe == recipe)
    return list((await session.execute(stmt)).scalars().all())


async def latest_for_region(
    session: AsyncSession, *, region_code: str, recipe: str, max_age_s: int = 900
) -> PredictionRun | None:
    """최근 실행이 있으면 재사용한다. H100 한 장이라 같은 격자를 반복해 돌릴 이유가 없다."""
    cutoff = datetime.now(UTC).timestamp() - max_age_s
    stmt = (
        select(PredictionRun)
        .where(
            PredictionRun.region_code == region_code,
            PredictionRun.recipe == recipe,
            PredictionRun.status == "succeeded",
        )
        .order_by(PredictionRun.requested_at.desc())
        .limit(1)
    )
    run_row = (await session.execute(stmt)).scalars().first()
    if run_row is None:
        return None
    if run_row.requested_at.timestamp() < cutoff:
        return None
    return run_row
