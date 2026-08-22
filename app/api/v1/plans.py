"""대피계획 — 기안·승인·개정.

승인은 approver 권한이 필요하다. 계획을 만든 사람과 승인한 사람이 같아도 되지만,
그 사실이 감사 이력에 남는다.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, Db, RequireApprover, RequireOperator, actor_name
from app.api.route import TransactionalRoute
from app.db.models import Community, Shelter
from app.schemas.plan import PlanApprove, PlanCreate, PlanItemOut, PlanOut
from app.services import incidents, planning

router = APIRouter(tags=["plans"], route_class=TransactionalRoute)


async def _to_out(session, plan) -> PlanOut:
    community_ids = [item.community_id for item in plan.items]
    shelter_ids = [item.shelter_id for item in plan.items if item.shelter_id]

    names: dict[uuid.UUID, str] = {}
    if community_ids:
        rows = await session.execute(
            select(Community.id, Community.name).where(Community.id.in_(community_ids))
        )
        names = dict(rows.all())

    shelter_names: dict[uuid.UUID, str] = {}
    if shelter_ids:
        rows = await session.execute(
            select(Shelter.id, Shelter.name).where(Shelter.id.in_(shelter_ids))
        )
        shelter_names = dict(rows.all())

    out = PlanOut.model_validate(plan)
    out.items = [
        PlanItemOut.model_validate(item).model_copy(
            update={
                "community_name": names.get(item.community_id),
                "shelter_name": shelter_names.get(item.shelter_id) if item.shelter_id else None,
            }
        )
        for item in plan.items
    ]
    out.is_actionable = plan.is_actionable
    out.notice = planning.PLAN_NOTICE
    return out


@router.post(
    "/incidents/{incident_id}/plans",
    response_model=PlanOut,
    status_code=status.HTTP_201_CREATED,
    summary="대피계획 기안",
    description=(
        "배열 순서가 대피 순서다. `rationale` 을 비워 두면 나중에 '왜 이 마을이 먼저인가'에 "
        "답할 수 없다. 새 계획을 기안하면 직전 계획은 `superseded` 가 되고 승인 이력은 남는다."
    ),
)
async def create_plan(
    incident_id: uuid.UUID,
    payload: PlanCreate,
    session: Db,
    current: RequireOperator,
    request: Request,
) -> PlanOut:
    incident = await incidents.get(session, incident_id)
    plan = await planning.create(session, incident, payload, actor=actor_name(request, current))
    return await _to_out(session, plan)


@router.get(
    "/incidents/{incident_id}/plans",
    response_model=list[PlanOut],
    summary="계획 목록 (버전 내림차순)",
)
async def list_plans(incident_id: uuid.UUID, session: Db, _: CurrentPrincipal) -> list[PlanOut]:
    await incidents.get(session, incident_id)
    plans = await planning.list_for_incident(session, incident_id)
    return [await _to_out(session, plan) for plan in plans]


@router.get(
    "/incidents/{incident_id}/plans/current",
    response_model=PlanOut | None,
    summary="지금 유효한 계획",
)
async def get_current_plan(
    incident_id: uuid.UUID, session: Db, _: CurrentPrincipal
) -> PlanOut | None:
    await incidents.get(session, incident_id)
    plan = await planning.current_plan(session, incident_id)
    return await _to_out(session, plan) if plan else None


@router.get("/plans/{plan_id}", response_model=PlanOut, summary="계획 하나")
async def get_plan(plan_id: uuid.UUID, session: Db, _: CurrentPrincipal) -> PlanOut:
    plan = await planning.get(session, plan_id)
    return await _to_out(session, plan)


@router.post(
    "/plans/{plan_id}/approve",
    response_model=PlanOut,
    summary="계획 승인 — approver 권한 필요",
    description=(
        "승인해야 주민 연락을 개시할 수 있다. 승인자 이름은 필수다 — "
        "감사 이력에 역할만 남으면 누가 결정했는지 알 수 없다."
    ),
)
async def approve_plan(
    plan_id: uuid.UUID,
    payload: PlanApprove,
    session: Db,
    current: RequireApprover,
    request: Request,
) -> PlanOut:
    plan = await planning.approve(session, plan_id, payload, actor=actor_name(request, current))
    return await _to_out(session, plan)
