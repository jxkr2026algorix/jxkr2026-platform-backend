"""주민 연락 — 명단 생성과 결과 기록.

**발송은 이 시스템이 하지 않는다.** GB SafeData 와 마찬가지로 여기에도 전화·문자를 보내는
경로는 없다. 남는 것은 누구에게 무엇을 시도했고 무엇을 확인했는가다.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from app.api.deps import CurrentPrincipal, Db, RequireOperator, actor_name
from app.api.route import TransactionalRoute
from app.db.models import Community
from app.schemas.contact import ContactOut, ContactRollup, ContactStart, ContactUpdate
from app.services import contacts, incidents

router = APIRouter(tags=["contacts"], route_class=TransactionalRoute)


async def _with_names(session, attempts) -> list[ContactOut]:
    if not attempts:
        return []
    ids = [a.community_id for a in attempts]
    rows = await session.execute(select(Community.id, Community.name).where(Community.id.in_(ids)))
    names = dict(rows.all())
    return [
        ContactOut.model_validate(a).model_copy(
            update={"community_name": names.get(a.community_id)}
        )
        for a in attempts
    ]


@router.post(
    "/incidents/{incident_id}/contacts",
    response_model=list[ContactOut],
    status_code=status.HTTP_201_CREATED,
    summary="주민 연락 개시 — 승인된 계획이 있어야 한다",
    description=(
        "승인 전에는 409 를 돌려준다. 승인되지 않은 순서로 주민을 움직이면 되돌릴 수 없다."
    ),
)
async def start_contacts(
    incident_id: uuid.UUID,
    payload: ContactStart,
    session: Db,
    current: RequireOperator,
    request: Request,
) -> list[ContactOut]:
    incident = await incidents.get(session, incident_id)
    created = await contacts.start_round(
        session, incident, payload, actor=actor_name(request, current)
    )
    return await _with_names(session, created)


@router.get(
    "/incidents/{incident_id}/contacts",
    response_model=list[ContactOut],
    summary="연락 현황",
)
async def list_contacts(
    incident_id: uuid.UUID, session: Db, _: CurrentPrincipal
) -> list[ContactOut]:
    await incidents.get(session, incident_id)
    attempts = await contacts.list_for_incident(session, incident_id)
    return await _with_names(session, attempts)


@router.get(
    "/incidents/{incident_id}/contacts/rollup",
    response_model=ContactRollup,
    summary="연락 집계 — 미연락을 따로 센다",
    description=(
        "`unreachable` 을 전체에 섞으면 '연락 완료 80%'가 되고, 갈 곳 없는 사람이 "
        "나머지 20% 안에서 보이지 않게 된다. 그래서 따로 센다."
    ),
)
async def contact_rollup(incident_id: uuid.UUID, session: Db, _: CurrentPrincipal) -> ContactRollup:
    await incidents.get(session, incident_id)
    attempts = await contacts.list_for_incident(session, incident_id)
    return contacts.rollup(attempts)


@router.patch(
    "/contacts/{contact_id}",
    response_model=ContactOut,
    summary="연락 결과 기록",
)
async def update_contact(
    contact_id: uuid.UUID,
    payload: ContactUpdate,
    session: Db,
    current: RequireOperator,
    request: Request,
) -> ContactOut:
    attempt = await contacts.update(
        session, contact_id, payload, actor=actor_name(request, current)
    )
    result = await _with_names(session, [attempt])
    return result[0]
