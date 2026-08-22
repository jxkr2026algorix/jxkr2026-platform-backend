"""현장 임무와 보고.

현장 보고가 접근 제약을 싣고 오면 승인된 계획이 `reapproval_required` 로 바뀐다.
이미 틀린 계획을 화면이 계속 옳다고 말하지 않게 하기 위해서다.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, Db, RequireField, RequireOperator, actor_name
from app.api.route import TransactionalRoute
from app.db.models import Community
from app.schemas.task import ReportCreate, ReportOut, TaskCreate, TaskOut, TaskUpdate
from app.services import incidents, tasks

router = APIRouter(tags=["field"], route_class=TransactionalRoute)


async def _with_names(session, rows) -> list[TaskOut]:
    if not rows:
        return []
    ids = [r.community_id for r in rows if r.community_id]
    names: dict[uuid.UUID, str] = {}
    if ids:
        result = await session.execute(
            select(Community.id, Community.name).where(Community.id.in_(ids))
        )
        names = dict(result.all())
    return [
        TaskOut.model_validate(r).model_copy(
            update={
                "community_name": names.get(r.community_id) if r.community_id else None,
                "report_count": len(r.reports),
            }
        )
        for r in rows
    ]


@router.post(
    "/incidents/{incident_id}/tasks",
    response_model=TaskOut,
    status_code=status.HTTP_201_CREATED,
    summary="현장 임무 등록",
)
async def create_task(
    incident_id: uuid.UUID,
    payload: TaskCreate,
    session: Db,
    current: RequireOperator,
    request: Request,
) -> TaskOut:
    incident = await incidents.get(session, incident_id)
    task = await tasks.create(session, incident, payload, actor=actor_name(request, current))
    result = await _with_names(session, [task])
    return result[0]


@router.post(
    "/incidents/{incident_id}/tasks/from-unreachable",
    response_model=list[TaskOut],
    summary="미연락 마을을 현장 확인 임무로 전환",
    description="미확인 세대는 저절로 사라지지 않는다. 명단에서 지우는 대신 사람이 가서 확인한다.",
)
async def generate_tasks(
    incident_id: uuid.UUID,
    session: Db,
    current: RequireOperator,
    request: Request,
) -> list[TaskOut]:
    incident = await incidents.get(session, incident_id)
    created = await tasks.create_from_unreachable(
        session, incident, actor=actor_name(request, current)
    )
    return await _with_names(session, created)


@router.get(
    "/incidents/{incident_id}/tasks",
    response_model=list[TaskOut],
    summary="임무 목록 (우선순위 순)",
)
async def list_tasks(
    incident_id: uuid.UUID,
    session: Db,
    _: CurrentPrincipal,
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[TaskOut]:
    await incidents.get(session, incident_id)
    rows = await tasks.list_for_incident(session, incident_id, status=status_filter)
    return await _with_names(session, rows)


@router.patch("/tasks/{task_id}", response_model=TaskOut, summary="임무 배정·상태 변경")
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    session: Db,
    current: RequireField,
    request: Request,
) -> TaskOut:
    task = await tasks.update(session, task_id, payload, actor=actor_name(request, current))
    result = await _with_names(session, [task])
    return result[0]


@router.post(
    "/tasks/{task_id}/reports",
    response_model=ReportOut,
    status_code=status.HTTP_201_CREATED,
    summary="현장 보고 제출 — 계획을 되돌릴 수 있다",
    description=(
        "`access_constraints` 가 하나라도 있거나 `request_replan=true` 면 승인된 계획이 "
        "`reapproval_required` 로 바뀌고, 그때까지 주민 연락 개시가 막힌다."
    ),
)
async def submit_report(
    task_id: uuid.UUID,
    payload: ReportCreate,
    session: Db,
    current: RequireField,
    request: Request,
) -> ReportOut:
    report = await tasks.submit_report(
        session, task_id, payload, actor=actor_name(request, current)
    )
    return ReportOut.model_validate(report)


@router.get(
    "/incidents/{incident_id}/reports",
    response_model=list[ReportOut],
    summary="현장 보고 목록",
)
async def list_reports(incident_id: uuid.UUID, session: Db, _: CurrentPrincipal) -> list[ReportOut]:
    await incidents.get(session, incident_id)
    rows = await tasks.list_reports(session, incident_id)
    return [ReportOut.model_validate(r) for r in rows]
