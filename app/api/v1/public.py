"""주민·현장용 공개 화면 데이터.

모바일 PWA 가 쓴다. **개인정보를 담지 않는다** — 집계와 공개 대피소 정보뿐이다.
공개 집계통계로 개인의 이동능력을 추정하면 안 된다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import Db, GbSafe
from app.api.route import TransactionalRoute
from app.db.models import Incident
from app.schemas.common import DataState
from app.services import situation

router = APIRouter(prefix="/public", tags=["public"], route_class=TransactionalRoute)


@router.get(
    "/status",
    summary="주민 화면용 요약 (인증 불필요)",
    description=(
        "지금 이 지역에 열린 상황이 있는지, 그리고 관측이 확인 가능한 상태인지만 준다. "
        "`state=UNVERIFIED` 를 '안전'으로 그리면 안 된다."
    ),
)
async def public_status(
    session: Db,
    client: GbSafe,
    region: str = Query(description="경북 시군"),
) -> dict:
    resolved = await situation.resolve_region(client, region)
    region_name = resolved.name or region

    rows = (
        (
            await session.execute(
                select(Incident)
                .where(Incident.region_name == region_name, Incident.status != "closed")
                .order_by(Incident.declared_at.desc())
            )
        )
        .scalars()
        .all()
    )

    state = DataState.UNVERIFIED
    caveat = None
    try:
        envelope = await client.hazard_context(region_name)
        state = envelope.state
        caveat = situation.headline_caveat(envelope, None)
    except Exception as exc:
        caveat = (
            f"현재 관측을 확인하지 못했습니다 ({type(exc).__name__}) — 안전하다는 뜻이 아닙니다"
        )

    return {
        "region": {"code": resolved.code, "name": region_name},
        "active_incidents": [
            {
                "code": i.code,
                "hazard": i.hazard,
                "level": i.level,
                "title": i.title,
                "declared_at": i.declared_at,
            }
            for i in rows
        ],
        "observation_state": state,
        "caveat": caveat,
        "emergency_numbers": {
            "fire_rescue": "119",
            "police": "112",
            "disaster": "행정안전부 재난안전",
        },
        "notice": "이 화면은 안내입니다. 대피 지시는 담당 공무원과 재난문자를 따르세요.",
        "generated_at": datetime.now(UTC),
    }
