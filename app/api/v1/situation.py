"""상황판 — 관측 조회. 전부 읽기 전용이다."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import CurrentPrincipal, Db, GbSafe
from app.api.route import TransactionalRoute
from app.db.models import Incident
from app.schemas.situation import (
    SituationContext,
    SituationOverview,
    SourceProbe,
    WeatherSnapshot,
)
from app.services import situation

router = APIRouter(prefix="/situation", tags=["situation"], route_class=TransactionalRoute)


@router.get(
    "/context",
    response_model=SituationContext,
    summary="특정 지역·재난의 현재 상황",
    description=(
        "여러 원천을 병렬 조회해 합친 봉투를 **줄이지 않고** 돌려준다.\n\n"
        "화면은 `state` 하나로 색을 정하면 된다.\n"
        "- `DATA` 값이 있다\n"
        "- `NONE` 조회 성공 + 부재 확인 — '발효 중 없음'\n"
        "- `UNVERIFIED` 확인 불가 — **안심시키는 색을 쓰면 안 된다**\n\n"
        "`records` 가 비었다고 위험이 없는 것이 아니다. `absence_confirmed` 를 읽어야 한다."
    ),
)
async def get_context(
    client: GbSafe,
    _: CurrentPrincipal,
    region: str = Query(description="경북 시군 (예: 문경시)"),
    hazard: str | None = Query(default=None, description="heavy_rain, landslide, wildfire ..."),
) -> SituationContext:
    return await situation.context(client, region_query=region, hazard=hazard)


@router.get(
    "/overview",
    response_model=SituationOverview,
    summary="콘솔 첫 화면 묶음 — ready 재난 5종을 한 번에",
    description=(
        "재난 하나가 실패해도 나머지를 돌려준다. 실패한 재난은 `state=UNVERIFIED` 로 남고 "
        "`unverified_sources` 에 사유가 실린다 — 조용히 지우지 않는다."
    ),
)
async def get_overview(
    client: GbSafe,
    session: Db,
    _: CurrentPrincipal,
    region: str = Query(description="경북 시군"),
) -> SituationOverview:
    open_count = (
        await session.execute(
            select(func.count()).select_from(Incident).where(Incident.status != "closed")
        )
    ).scalar_one()
    return await situation.overview(client, region_query=region, open_incidents=open_count)


@router.get(
    "/sources/{connector}",
    response_model=SourceProbe,
    summary="원천 하나를 직접 조회",
    description="사용 가능한 커넥터 이름은 `/api/v1/situation/health` 의 connectors 에 있다.",
)
async def probe_source(
    connector: str,
    client: GbSafe,
    _: CurrentPrincipal,
    region: str | None = Query(default=None),
    rows: int | None = Query(default=None),
) -> SourceProbe:
    envelope = await client.source(connector, region=region, rows=rows)
    return SourceProbe(
        connector=connector,
        region=region,
        envelope=envelope,
        state=envelope.state,
        fetched_at=datetime.now(UTC),
    )


@router.get(
    "/weather",
    response_model=WeatherSnapshot,
    summary="현재 기상 — 화면이 바로 쓰는 모양",
    description=(
        "기상청 실황·단기예보를 시군 하나 분으로 추려 준다. 기온·습도·풍속·강수는 "
        "꺼내 두고, 나머지 관측은 `readings` 로 함께 나간다.\n\n"
        "**값이 없으면 지어내지 않는다.** `state=UNVERIFIED` 면 못 읽은 것이고, 화면은 "
        "그걸 '맑음'이나 0 으로 그리면 안 된다. `stale=true` 면 갱신주기를 넘긴 값이므로 "
        "`observed_at` 을 함께 띄워야 한다.\n\n"
        "`attribution` 은 출처 표기 문구다 — KOGL 이므로 화면에서 지우면 안 된다."
    ),
)
async def get_weather(
    client: GbSafe,
    _: CurrentPrincipal,
    region: str = Query(description="경북 시군 (예: 청송군)"),
) -> WeatherSnapshot:
    return await situation.weather(client, region_query=region)


@router.get("/health", summary="상류 원천 상태 — 무엇이 왜 안 되는지")
async def upstream_health(client: GbSafe, _: CurrentPrincipal) -> dict:
    return await client.health()
