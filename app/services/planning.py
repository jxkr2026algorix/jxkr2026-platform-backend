"""대피계획 서비스.

절차가 곧 안전장치다.

    draft ──approve──▶ approved ──현장보고──▶ reapproval_required ──revise──▶ (새 버전) draft
                                     │
                                     └─ 승인된 계획만 주민 연락을 개시할 수 있다

승인된 계획을 **직접 수정하지 않는다.** 개정은 항상 새 버전이다. 그래야 "누가 무엇을
승인했는가"가 나중에도 남는다.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.base import utcnow
from app.db.models import Community, EvacuationPlan, Incident, PlanItem, Shelter
from app.schemas.plan import PlanApprove, PlanCreate
from app.services import audit

PLAN_NOTICE = (
    "이 계획의 순서는 노출·이동지원 필요·접근 제약에서 나왔습니다. "
    "실시간 도로 통제와 현장 확인이 반영되기 전까지는 제안이며, 최종 판단은 담당자가 합니다."
)


async def _validate_items(session: AsyncSession, payload: PlanCreate) -> None:
    community_ids = [item.community_id for item in payload.items]
    if len(set(community_ids)) != len(community_ids):
        raise ValidationError("같은 마을이 계획에 두 번 들어 있습니다")

    found = set(
        (await session.execute(select(Community.id).where(Community.id.in_(community_ids))))
        .scalars()
        .all()
    )
    missing = set(community_ids) - found
    if missing:
        raise ValidationError(f"존재하지 않는 마을: {sorted(str(m) for m in missing)}")

    shelter_ids = [item.shelter_id for item in payload.items if item.shelter_id]
    if shelter_ids:
        found_shelters = set(
            (await session.execute(select(Shelter.id).where(Shelter.id.in_(shelter_ids))))
            .scalars()
            .all()
        )
        missing_shelters = set(shelter_ids) - found_shelters
        if missing_shelters:
            raise ValidationError(
                f"존재하지 않는 대피소: {sorted(str(m) for m in missing_shelters)}"
            )


async def create(
    session: AsyncSession, incident: Incident, payload: PlanCreate, *, actor: str
) -> EvacuationPlan:
    await _validate_items(session, payload)

    version = (
        await session.scalar(
            select(func.coalesce(func.max(EvacuationPlan.version), 0)).where(
                EvacuationPlan.incident_id == incident.id
            )
        )
    ) + 1

    # 직전 계획은 자리를 물려준다. 승인 이력은 지우지 않고 superseded 로 남긴다.
    previous = await current_plan(session, incident.id)

    plan = EvacuationPlan(
        incident_id=incident.id,
        version=version,
        status="draft",
        rationale=payload.rationale,
        created_by=actor,
        evidence=payload.evidence,
    )
    session.add(plan)
    await session.flush()

    for index, item in enumerate(payload.items):
        session.add(
            PlanItem(
                plan_id=plan.id,
                order_index=index,
                community_id=item.community_id,
                shelter_id=item.shelter_id,
                residents=item.residents,
                transport=item.transport,
                action=item.action,
                rationale=item.rationale,
                evidence=item.evidence,
            )
        )

    if previous is not None and previous.id != plan.id:
        previous.status = "superseded"
        previous.superseded_by = plan.id

    await session.flush()
    await session.refresh(plan)

    await audit.record(
        session,
        actor=actor,
        action="plan.drafted",
        entity_type="plan",
        entity_id=str(plan.id),
        incident_id=incident.id,
        summary=f"대피계획 v{version} 기안 ({len(payload.items)}개 마을)",
        payload={"version": version, "items": len(payload.items)},
    )
    return plan


async def get(session: AsyncSession, plan_id: uuid.UUID) -> EvacuationPlan:
    plan = await session.get(EvacuationPlan, plan_id)
    if plan is None:
        raise NotFoundError(f"계획 {plan_id} 를 찾지 못했습니다")
    return plan


async def list_for_incident(session: AsyncSession, incident_id: uuid.UUID) -> list[EvacuationPlan]:
    stmt = (
        select(EvacuationPlan)
        .where(EvacuationPlan.incident_id == incident_id)
        .order_by(EvacuationPlan.version.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def current_plan(session: AsyncSession, incident_id: uuid.UUID) -> EvacuationPlan | None:
    """지금 유효한 계획 — superseded 가 아닌 최신 버전."""
    stmt = (
        select(EvacuationPlan)
        .where(
            EvacuationPlan.incident_id == incident_id,
            EvacuationPlan.status != "superseded",
        )
        .order_by(EvacuationPlan.version.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def approve(
    session: AsyncSession, plan_id: uuid.UUID, payload: PlanApprove, *, actor: str
) -> EvacuationPlan:
    plan = await get(session, plan_id)

    if plan.status == "approved":
        raise ConflictError(f"계획 v{plan.version} 는 이미 승인되었습니다")
    if plan.status == "superseded":
        raise ConflictError(
            f"계획 v{plan.version} 는 v{plan.superseded_by} 로 대체되었습니다 — "
            "최신 버전을 승인하세요"
        )

    plan.status = "approved"
    plan.approved_by = payload.approver
    plan.approved_at = utcnow()
    await session.flush()

    await audit.record(
        session,
        actor=actor,
        action="plan.approved",
        entity_type="plan",
        entity_id=str(plan.id),
        incident_id=plan.incident_id,
        summary=f"대피계획 v{plan.version} 승인 — 승인자 {payload.approver}",
        payload={
            "version": plan.version,
            "approver": payload.approver,
            "note": payload.note,
            "acknowledged_caveats": payload.acknowledge_caveats,
        },
    )
    return plan


async def mark_needs_reapproval(
    session: AsyncSession, incident_id: uuid.UUID, *, reason: str, actor: str
) -> EvacuationPlan | None:
    """현장 보고가 승인된 계획을 무효화한다.

    계획을 지우지 않는다 — 상태만 바꾼다. 화면이 "지금 이 계획은 다시 승인받아야 한다"를
    말할 수 있어야 하고, 그때까지 연락 개시는 막힌다.
    """
    plan = await current_plan(session, incident_id)
    if plan is None or plan.status != "approved":
        return plan

    plan.status = "reapproval_required"
    await session.flush()

    await audit.record(
        session,
        actor=actor,
        action="plan.reapproval_required",
        entity_type="plan",
        entity_id=str(plan.id),
        incident_id=incident_id,
        summary=f"계획 v{plan.version} 재승인 필요 — {reason}",
        payload={"version": plan.version, "reason": reason},
    )
    return plan
