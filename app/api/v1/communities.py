"""마을과 대피소."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentPrincipal, Db, RequireOperator
from app.api.route import TransactionalRoute
from app.schemas.common import Page
from app.schemas.community import (
    CommunityCreate,
    CommunityOut,
    MapPoint,
    ShelterCreate,
    ShelterOut,
)
from app.services import communities

router = APIRouter(tags=["communities"], route_class=TransactionalRoute)


def _community_out(row) -> CommunityOut:
    out = CommunityOut.model_validate(row)
    if row.map_x is not None and row.map_y is not None:
        out.map_point = MapPoint(x=row.map_x, y=row.map_y)
    return out


@router.get("/communities", response_model=Page[CommunityOut], summary="마을 목록")
async def list_communities(
    session: Db,
    _: CurrentPrincipal,
    region_code: str | None = Query(default=None),
    limit: int = Query(default=200, le=500),
    offset: int = Query(default=0, ge=0),
) -> Page[CommunityOut]:
    rows, total = await communities.list_communities(
        session, region_code=region_code, limit=limit, offset=offset
    )
    return Page(items=[_community_out(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/communities/{community_id}", response_model=CommunityOut, summary="마을 하나")
async def get_community(community_id: uuid.UUID, session: Db, _: CurrentPrincipal) -> CommunityOut:
    return _community_out(await communities.get_community(session, community_id))


@router.post(
    "/communities",
    response_model=CommunityOut,
    status_code=status.HTTP_201_CREATED,
    summary="마을 등록",
)
async def create_community(
    payload: CommunityCreate, session: Db, _: RequireOperator
) -> CommunityOut:
    return _community_out(await communities.create_community(session, payload))


@router.get(
    "/shelters",
    response_model=list[ShelterOut],
    summary="대피소 조회 — hazard 필수",
    description=(
        "**hazard 없이 대피소를 묻지 않는다.** 지진 대피소와 호우 대피소는 다른 시설이고, "
        "화학사고는 화학물질관리법 제23조의4 법정 지정 대피장소가 따로 있다. "
        "정원(`capacity`)은 연 1회 갱신 파일 기준이라 실시간 수용현황이 아니다."
    ),
)
async def find_shelters(
    session: Db,
    _: CurrentPrincipal,
    hazard: str = Query(description="이 재난에 쓸 수 있는 대피소만 (필수)"),
    region_code: str | None = Query(default=None),
    lat: float | None = Query(default=None),
    lon: float | None = Query(default=None),
    limit: int = Query(default=20, le=100),
) -> list[ShelterOut]:
    pairs = await communities.find_shelters(
        session, hazard=hazard, region_code=region_code, lat=lat, lon=lon, limit=limit
    )
    result = []
    for shelter, distance in pairs:
        out = ShelterOut.model_validate(shelter)
        out.distance_km = distance
        result.append(out)
    return result


@router.post(
    "/shelters",
    response_model=ShelterOut,
    status_code=status.HTTP_201_CREATED,
    summary="대피소 등록",
)
async def create_shelter(payload: ShelterCreate, session: Db, _: RequireOperator) -> ShelterOut:
    return ShelterOut.model_validate(await communities.create_shelter(session, payload))
