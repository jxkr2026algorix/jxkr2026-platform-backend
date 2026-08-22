"""현장 임무·보고 서비스."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.base import utcnow
from app.db.models import Community, ContactAttempt, FieldReport, FieldTask, Incident
from app.schemas.task import ReportCreate, TaskCreate, TaskUpdate
from app.services import audit, planning


async def create(
    session: AsyncSession, incident: Incident, payload: TaskCreate, *, actor: str
) -> FieldTask:
    task = FieldTask(
        incident_id=incident.id,
        community_id=payload.community_id,
        title=payload.title,
        detail=payload.detail,
        kind=payload.kind,
        priority=payload.priority,
        status="assigned" if payload.assignee else "unassigned",
        assignee=payload.assignee,
        due_at=payload.due_at,
        created_by=actor,
        # 새로 만든 객체는 관계가 '미로드' 상태라, 직렬화 중에 지연 로드가 일어나면
        # async 컨텍스트 밖에서 IO 를 시도해 MissingGreenlet 으로 터진다.
        reports=[],
    )
    session.add(task)
    await session.flush()

    await audit.record(
        session,
        actor=actor,
        action="task.created",
        entity_type="task",
        entity_id=str(task.id),
        incident_id=incident.id,
        summary=f"현장 임무 등록: {payload.title}",
        payload={"kind": payload.kind, "priority": payload.priority},
    )
    return task


async def create_from_unreachable(
    session: AsyncSession, incident: Incident, *, actor: str
) -> list[FieldTask]:
    """연락되지 않은 마을을 현장 확인 임무로 넘긴다.

    미확인 세대는 저절로 사라지지 않는다. 명단에서 지우는 대신 사람이 가서 확인한다.
    """
    stmt = select(ContactAttempt).where(
        ContactAttempt.incident_id == incident.id,
        ContactAttempt.response == "unreachable",
    )
    unreachable = list((await session.execute(stmt)).scalars().all())
    if not unreachable:
        return []

    existing = set(
        (
            await session.execute(
                select(FieldTask.community_id).where(
                    FieldTask.incident_id == incident.id,
                    FieldTask.kind == "verify_household",
                    FieldTask.status.notin_(("done", "cancelled")),
                )
            )
        )
        .scalars()
        .all()
    )

    created: list[FieldTask] = []
    for attempt in unreachable:
        if attempt.community_id in existing:
            continue
        community = await session.get(Community, attempt.community_id)
        name = community.name if community else str(attempt.community_id)
        task = FieldTask(
            incident_id=incident.id,
            community_id=attempt.community_id,
            title=f"미연락 세대 확인 — {name}",
            detail=(
                f"{attempt.households}세대 중 확인 "
                f"{attempt.households_confirmed}세대. 현장 확인 필요"
            ),
            kind="verify_household",
            priority=1,
            status="unassigned",
            created_by=actor,
            reports=[],
        )
        session.add(task)
        created.append(task)

    if created:
        await session.flush()
        await audit.record(
            session,
            actor=actor,
            action="task.generated",
            entity_type="task",
            incident_id=incident.id,
            summary=f"미연락 마을 {len(created)}곳을 현장 확인 임무로 전환",
            payload={"count": len(created)},
        )
    return created


async def get(session: AsyncSession, task_id: uuid.UUID) -> FieldTask:
    task = await session.get(FieldTask, task_id)
    if task is None:
        raise NotFoundError(f"임무 {task_id} 를 찾지 못했습니다")
    return task


async def list_for_incident(
    session: AsyncSession, incident_id: uuid.UUID, *, status: str | None = None
) -> list[FieldTask]:
    stmt = (
        select(FieldTask)
        .where(FieldTask.incident_id == incident_id)
        .order_by(FieldTask.priority, FieldTask.created_at)
    )
    if status:
        stmt = stmt.where(FieldTask.status == status)
    return list((await session.execute(stmt)).scalars().all())


async def update(
    session: AsyncSession, task_id: uuid.UUID, payload: TaskUpdate, *, actor: str
) -> FieldTask:
    task = await get(session, task_id)
    changes: dict[str, object] = {}
    for field in ("status", "assignee", "priority", "detail", "due_at"):
        value = getattr(payload, field)
        if value is not None and value != getattr(task, field):
            setattr(task, field, value)
            changes[field] = str(value)
    if payload.assignee and payload.status is None and task.status == "unassigned":
        task.status = "assigned"
        changes["status"] = "assigned"

    if changes:
        await session.flush()
        await audit.record(
            session,
            actor=actor,
            action="task.updated",
            entity_type="task",
            entity_id=str(task.id),
            incident_id=task.incident_id,
            summary=f"임무 변경: {task.title} ({', '.join(changes)})",
            payload=changes,
        )
    return task


async def submit_report(
    session: AsyncSession, task_id: uuid.UUID, payload: ReportCreate, *, actor: str
) -> FieldReport:
    task = await get(session, task_id)

    report = FieldReport(
        task_id=task.id,
        incident_id=task.incident_id,
        body=payload.body,
        observation=payload.observation,
        households_verified=payload.households_verified,
        access_constraints=[c.model_dump() for c in payload.access_constraints],
        submitted_by=actor,
        submitted_at=utcnow(),
        triggered_replan=False,
    )
    session.add(report)

    if task.status not in ("done", "cancelled"):
        task.status = "in_review"

    await session.flush()

    # 통제 구간이 새로 확인되었거나 현장이 재계획을 요청하면 승인된 계획을 되돌린다.
    needs_replan = payload.request_replan or bool(payload.access_constraints)
    if needs_replan:
        reason = (
            "현장 재계획 요청"
            if payload.request_replan
            else f"접근 제약 {len(payload.access_constraints)}건 확인"
        )
        plan = await planning.mark_needs_reapproval(
            session, task.incident_id, reason=reason, actor=actor
        )
        report.triggered_replan = plan is not None and plan.status == "reapproval_required"

    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="report.submitted",
        entity_type="report",
        entity_id=str(report.id),
        incident_id=task.incident_id,
        summary=f"현장 보고 접수: {task.title}",
        payload={
            "observation": payload.observation,
            "constraints": len(payload.access_constraints),
            "triggered_replan": report.triggered_replan,
        },
    )
    return report


async def list_reports(session: AsyncSession, incident_id: uuid.UUID) -> list[FieldReport]:
    stmt = (
        select(FieldReport)
        .where(FieldReport.incident_id == incident_id)
        .order_by(FieldReport.submitted_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())
