"""주민 연락 서비스.

승인 전에는 연락을 개시할 수 없다. 이건 UI 편의가 아니라 절차다 —
승인되지 않은 순서로 주민을 움직이면 되돌릴 방법이 없다.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.db.base import utcnow
from app.db.models import Community, ContactAttempt, Incident
from app.schemas.contact import ContactRollup, ContactStart, ContactUpdate
from app.services import audit, planning


async def start_round(
    session: AsyncSession, incident: Incident, payload: ContactStart, *, actor: str
) -> list[ContactAttempt]:
    plan = await planning.current_plan(session, incident.id)
    if plan is None:
        raise ConflictError("대피계획이 없습니다 — 계획을 먼저 기안하세요")
    if plan.status != "approved":
        raise ConflictError(
            f"계획 v{plan.version} 상태가 {plan.status} 입니다 — "
            "승인 후에 연락을 개시할 수 있습니다"
        )

    existing = set(
        (
            await session.execute(
                select(ContactAttempt.community_id).where(
                    ContactAttempt.incident_id == incident.id,
                    ContactAttempt.plan_id == plan.id,
                )
            )
        )
        .scalars()
        .all()
    )

    created: list[ContactAttempt] = []
    for item in plan.items:
        if item.community_id in existing:
            continue
        community = await session.get(Community, item.community_id)
        households = community.households if community and community.households else 0
        attempt = ContactAttempt(
            incident_id=incident.id,
            plan_id=plan.id,
            community_id=item.community_id,
            households=households,
            households_confirmed=0,
            channel=payload.channel,
            response="pending",
            follow_up=payload.note,
            data_mode=community.data_mode if community else "synthetic",
        )
        session.add(attempt)
        created.append(attempt)

    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="contact.started",
        entity_type="contact",
        incident_id=incident.id,
        entity_id=str(plan.id),
        summary=f"주민 연락 개시 — 계획 v{plan.version}, 대상 {len(created)}개 마을",
        payload={"plan_version": plan.version, "channel": payload.channel, "created": len(created)},
    )
    return created


async def get(session: AsyncSession, contact_id: uuid.UUID) -> ContactAttempt:
    attempt = await session.get(ContactAttempt, contact_id)
    if attempt is None:
        raise NotFoundError(f"연락 기록 {contact_id} 를 찾지 못했습니다")
    return attempt


async def list_for_incident(session: AsyncSession, incident_id: uuid.UUID) -> list[ContactAttempt]:
    stmt = (
        select(ContactAttempt)
        .where(ContactAttempt.incident_id == incident_id)
        .order_by(ContactAttempt.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def update(
    session: AsyncSession, contact_id: uuid.UUID, payload: ContactUpdate, *, actor: str
) -> ContactAttempt:
    attempt = await get(session, contact_id)
    attempt.response = payload.response
    if payload.households_confirmed is not None:
        attempt.households_confirmed = payload.households_confirmed
    if payload.follow_up is not None:
        attempt.follow_up = payload.follow_up
    attempt.attempted_at = utcnow()
    attempt.recorded_by = actor
    await session.flush()

    await audit.record(
        session,
        actor=actor,
        action="contact.recorded",
        entity_type="contact",
        entity_id=str(attempt.id),
        incident_id=attempt.incident_id,
        summary=f"연락 결과 기록: {payload.response}",
        payload={"response": payload.response, "follow_up": payload.follow_up},
    )
    return attempt


def rollup(attempts: list[ContactAttempt]) -> ContactRollup:
    result = ContactRollup(total=len(attempts))
    for attempt in attempts:
        result.households_total += attempt.households
        result.households_confirmed += attempt.households_confirmed
        match attempt.response:
            case "pending":
                result.pending += 1
            case "evacuating":
                result.evacuating += 1
            case "support_requested":
                result.support_requested += 1
            case "refused":
                result.refused += 1
            case "unreachable":
                result.unreachable += 1
    result.needs_field_verification = result.unreachable
    return result
