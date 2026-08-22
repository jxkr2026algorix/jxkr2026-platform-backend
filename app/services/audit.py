"""감사 이력 기록.

되돌릴 수 없는 결정(승인·연락개시·보고접수)은 반드시 여기를 지난다.
화면의 타임라인이 이 표를 그대로 읽으므로, 사람이 읽을 문장을 `summary` 에 넣는다.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import utcnow
from app.db.models import AuditEvent


async def record(
    session: AsyncSession,
    *,
    actor: str,
    action: str,
    entity_type: str,
    summary: str,
    incident_id: uuid.UUID | None = None,
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        incident_id=incident_id,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
        payload=payload,
        created_at=utcnow(),
    )
    session.add(event)
    await session.flush()
    return event
