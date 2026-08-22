"""상황 — 개시·조회·변경·타임라인."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, status

from app.api.deps import CurrentPrincipal, Db, GbSafe, RequireOperator, actor_name
from app.api.route import TransactionalRoute
from app.schemas.common import Page
from app.schemas.hazard import korean_name
from app.schemas.incident import IncidentCreate, IncidentOut, IncidentUpdate, TimelineEvent
from app.services import incidents, situation

router = APIRouter(prefix="/incidents", tags=["incidents"], route_class=TransactionalRoute)


def _to_out(incident) -> IncidentOut:
    out = IncidentOut.model_validate(incident)
    out.hazard_korean = korean_name(incident.hazard)
    return out


@router.post(
    "",
    response_model=IncidentOut,
    status_code=status.HTTP_201_CREATED,
    summary="상황 개시",
    description=(
        "상황은 **사람이** 연다. 관측값이 임계를 넘었다는 이유로 시스템이 자동으로 열지 않는다. "
        "`opening_evidence` 에 그 시점 화면의 근거를 같이 남기면 나중에 재구성할 수 있다."
    ),
)
async def create_incident(
    payload: IncidentCreate,
    session: Db,
    client: GbSafe,
    current: RequireOperator,
    request: Request,
) -> IncidentOut:
    resolved = await situation.resolve_region(client, payload.region_code)
    region_name = resolved.name or payload.region_code
    incident = await incidents.create(
        session, payload, actor=actor_name(request, current), region_name=region_name
    )
    return _to_out(incident)


@router.get("", response_model=Page[IncidentOut], summary="상황 목록")
async def list_incidents(
    session: Db,
    _: CurrentPrincipal,
    status_filter: str | None = Query(default=None, alias="status"),
    region_code: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[IncidentOut]:
    rows, total = await incidents.list_incidents(
        session, status=status_filter, region_code=region_code, limit=limit, offset=offset
    )
    return Page(items=[_to_out(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/{incident_id}", response_model=IncidentOut, summary="상황 하나")
async def get_incident(incident_id: uuid.UUID, session: Db, _: CurrentPrincipal) -> IncidentOut:
    return _to_out(await incidents.get(session, incident_id))


@router.patch("/{incident_id}", response_model=IncidentOut, summary="상황 변경 (단계·종결)")
async def update_incident(
    incident_id: uuid.UUID,
    payload: IncidentUpdate,
    session: Db,
    current: RequireOperator,
    request: Request,
) -> IncidentOut:
    incident = await incidents.update(
        session, incident_id, payload, actor=actor_name(request, current)
    )
    return _to_out(incident)


@router.get(
    "/{incident_id}/timeline",
    response_model=list[TimelineEvent],
    summary="타임라인 — 누가 언제 무엇을 정했는가",
)
async def get_timeline(
    incident_id: uuid.UUID,
    session: Db,
    _: CurrentPrincipal,
    limit: int = Query(default=200, le=500),
) -> list[TimelineEvent]:
    rows = await incidents.timeline(session, incident_id, limit=limit)
    return [TimelineEvent.model_validate(r) for r in rows]
