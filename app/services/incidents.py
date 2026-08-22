"""상황 서비스."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.db.base import utcnow
from app.db.models import AuditEvent, Incident
from app.schemas.incident import IncidentCreate, IncidentUpdate
from app.services import audit


async def next_incident_code(session: AsyncSession) -> str:
    """YYYY-MMDD-NN. 같은 날 몇 번째 상황인지 사람이 바로 읽을 수 있어야 한다."""
    today = utcnow()
    prefix = f"{today:%Y-%m%d}"
    stmt = select(func.count()).select_from(Incident).where(Incident.code.startswith(prefix))
    count = (await session.execute(stmt)).scalar_one()
    return f"{prefix}-{count + 1:02d}"


async def create(
    session: AsyncSession,
    payload: IncidentCreate,
    *,
    actor: str,
    region_name: str,
) -> Incident:
    code = payload.code or await next_incident_code(session)
    existing = await session.scalar(select(Incident).where(Incident.code == code))
    if existing is not None:
        raise ConflictError(f"상황 코드 {code} 가 이미 있습니다")

    incident = Incident(
        code=code,
        title=payload.title,
        region_code=payload.region_code,
        region_name=region_name,
        hazard=payload.hazard.value,
        level=payload.level,
        status="open",
        summary=payload.summary,
        declared_by=actor,
        declared_at=utcnow(),
        opening_evidence=payload.opening_evidence,
    )
    session.add(incident)
    await session.flush()

    await audit.record(
        session,
        actor=actor,
        action="incident.declared",
        entity_type="incident",
        entity_id=str(incident.id),
        incident_id=incident.id,
        summary=f"{region_name} {payload.hazard.value} 상황 개시 (대응 {payload.level}단계)",
        payload={"code": code, "level": payload.level},
    )
    return incident


async def get(session: AsyncSession, incident_id: uuid.UUID) -> Incident:
    incident = await session.get(Incident, incident_id)
    if incident is None:
        raise NotFoundError(f"상황 {incident_id} 를 찾지 못했습니다")
    return incident


async def list_incidents(
    session: AsyncSession,
    *,
    status: str | None = None,
    region_code: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Incident], int]:
    stmt = select(Incident).order_by(Incident.declared_at.desc())
    count_stmt = select(func.count()).select_from(Incident)
    if status:
        stmt = stmt.where(Incident.status == status)
        count_stmt = count_stmt.where(Incident.status == status)
    if region_code:
        stmt = stmt.where(Incident.region_code == region_code)
        count_stmt = count_stmt.where(Incident.region_code == region_code)

    total = (await session.execute(count_stmt)).scalar_one()
    rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    return list(rows), total


async def update(
    session: AsyncSession, incident_id: uuid.UUID, payload: IncidentUpdate, *, actor: str
) -> Incident:
    incident = await get(session, incident_id)
    changes: dict[str, object] = {}

    if payload.title is not None and payload.title != incident.title:
        changes["title"] = payload.title
        incident.title = payload.title
    if payload.summary is not None and payload.summary != incident.summary:
        changes["summary"] = payload.summary
        incident.summary = payload.summary
    if payload.level is not None and payload.level != incident.level:
        changes["level"] = payload.level
        incident.level = payload.level
    if payload.status is not None and payload.status != incident.status:
        changes["status"] = payload.status
        incident.status = payload.status
        if payload.status == "closed":
            incident.closed_at = utcnow()
            incident.closed_by = actor
        else:
            incident.closed_at = None
            incident.closed_by = None

    if changes:
        await session.flush()
        await audit.record(
            session,
            actor=actor,
            action="incident.updated",
            entity_type="incident",
            entity_id=str(incident.id),
            incident_id=incident.id,
            summary=f"상황 {incident.code} 변경: {', '.join(changes)}",
            payload=changes,
        )
    return incident


async def timeline(
    session: AsyncSession, incident_id: uuid.UUID, *, limit: int = 200
) -> list[AuditEvent]:
    await get(session, incident_id)
    stmt = (
        select(AuditEvent)
        .where(AuditEvent.incident_id == incident_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())
