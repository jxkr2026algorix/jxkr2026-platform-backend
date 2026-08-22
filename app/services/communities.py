"""마을·대피소 조회.

`find_shelters` 는 반드시 hazard 를 받는다. 지진 대피소와 호우 대피소는 다른 시설이고,
화학사고는 화학물질관리법상 법정 지정 대피장소가 따로 있다. 자동 전용은 사고다.
"""

from __future__ import annotations

import math
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.models import Community, Shelter
from app.schemas.community import CommunityCreate, ShelterCreate


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_km = 6371.0088
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * radius_km * math.asin(math.sqrt(a))


async def list_communities(
    session: AsyncSession,
    *,
    region_code: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[Community], int]:
    stmt = select(Community).order_by(Community.name)
    count_stmt = select(func.count()).select_from(Community)
    if region_code:
        stmt = stmt.where(Community.region_code == region_code)
        count_stmt = count_stmt.where(Community.region_code == region_code)
    total = (await session.execute(count_stmt)).scalar_one()
    rows = (await session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    return list(rows), total


async def get_community(session: AsyncSession, community_id: uuid.UUID) -> Community:
    community = await session.get(Community, community_id)
    if community is None:
        raise NotFoundError(f"마을 {community_id} 를 찾지 못했습니다")
    return community


async def create_community(session: AsyncSession, payload: CommunityCreate) -> Community:
    community = Community(
        region_code=payload.region_code,
        region_name=payload.region_name,
        name=payload.name,
        name_en=payload.name_en,
        emd_name=payload.emd_name,
        residents=payload.residents,
        households=payload.households,
        assisted_mobility_estimate=payload.assisted_mobility_estimate,
        vulnerability_note=payload.vulnerability_note,
        lat=payload.lat,
        lon=payload.lon,
        map_x=payload.map_point.x if payload.map_point else None,
        map_y=payload.map_point.y if payload.map_point else None,
        notes=payload.notes,
        tags=payload.tags,
        data_mode=payload.data_mode,
    )
    session.add(community)
    await session.flush()
    return community


async def find_shelters(
    session: AsyncSession,
    *,
    hazard: str,
    region_code: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    limit: int = 20,
) -> list[tuple[Shelter, float | None]]:
    """이 재난에 쓸 수 있는 대피소만. 좌표를 주면 거리순으로 정렬한다."""
    stmt = select(Shelter)
    if region_code:
        stmt = stmt.where(Shelter.region_code == region_code)
    rows = list((await session.execute(stmt)).scalars().all())

    usable = [s for s in rows if hazard in (s.hazards or [])]

    scored: list[tuple[Shelter, float | None]] = []
    for shelter in usable:
        distance = None
        if (
            lat is not None
            and lon is not None
            and shelter.lat is not None
            and shelter.lon is not None
        ):
            distance = round(haversine_km(lat, lon, shelter.lat, shelter.lon), 3)
        scored.append((shelter, distance))

    if lat is not None and lon is not None:
        scored.sort(key=lambda pair: (pair[1] is None, pair[1] if pair[1] is not None else 0.0))
    else:
        scored.sort(key=lambda pair: pair[0].name)
    return scored[:limit]


async def create_shelter(session: AsyncSession, payload: ShelterCreate) -> Shelter:
    shelter = Shelter(**payload.model_dump())
    session.add(shelter)
    await session.flush()
    return shelter
