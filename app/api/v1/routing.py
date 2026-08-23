"""대피 경로.

**여기서 나오는 경로는 제안이다.** 실시간 통제가 모두 반영된 공식 안전경로가 아니다.
검증되지 않은 경로를 공식 안전경로로 표시하는 것은 데이터 계층이 명시적으로 금지한 항목이다.

도로망은 OSM 이고 산출물은 ODbL 파생물이다 — 응답의 `attribution` 을 지우면 안 된다.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import Config, CurrentPrincipal, Db, GbSafe, MlEngine, RequireOperator
from app.api.route import TransactionalRoute
from app.routing.profiles import PROFILES
from app.schemas.routing import ModeCatalog, ModeInfo, RoutePlan, RouteRequest
from app.services import routing

router = APIRouter(prefix="/routing", tags=["routing"], route_class=TransactionalRoute)


@router.get(
    "/modes",
    response_model=ModeCatalog,
    summary="이동수단과 통행 규칙",
    description=(
        "수단마다 지날 수 있는 길이 다르다. 모르는 태그는 통행 가능으로 두지 않는다 — "
        "통행 가능한 길을 빼는 쪽이 막힌 길로 보내는 쪽보다 안전하다."
    ),
)
async def list_modes(settings: Config, _: CurrentPrincipal) -> ModeCatalog:
    return ModeCatalog(
        modes=[
            ModeInfo(
                mode=profile.mode,
                korean_name=profile.korean_name,
                speed_kmh=round(profile.speed_mps * 3.6, 1),
                note=profile.note or None,
                grades=sorted(profile.grades),
            )
            for profile in PROFILES.values()
        ],
        road_network_loaded=routing.road_network_available(settings),
        road_network_source=settings.road_network_path or None,
    )


@router.post(
    "/evacuation",
    response_model=RoutePlan,
    summary="위험 구역을 피해 대피소로 가는 경로",
    description=(
        "재난에 맞는 대피소만 후보로 삼고, 이동수단이 지날 수 있는 길만 쓴다.\n\n"
        "**시간에 따라 커지는 위험을 반영한다.** 산불처럼 퍼지는 재난은 지금 안전한 길이 "
        "30분 뒤에는 아닐 수 있다. 예측을 여러 시점으로 받아, 각 지점을 **지나는 시각**의 "
        "위험으로 판단한다. 한 번 위험해진 칸은 계속 위험한 것으로 둔다 — 불이 지나간 자리를 "
        "안전으로 읽지 않기 위해서다.\n\n"
        "`incident_id` 를 주면 현장 보고에서 확인된 통제 구간이 **차단**으로 들어간다. "
        "예측은 확률이지만 현장 보고는 사람이 가서 본 것이라 더 강하다.\n\n"
        "경로를 못 찾으면 빈 배열이 아니라 항목마다 `reason` 이 실린다 — '가까운 대피소가 "
        "없다'와 '길이 전부 막혔다'는 다른 상태다."
    ),
)
async def plan_evacuation_route(
    payload: RouteRequest,
    session: Db,
    client: MlEngine,
    gbsafe: GbSafe,
    settings: Config,
    _: RequireOperator,
) -> RoutePlan:
    return await routing.plan_evacuation(session, client, settings, payload, gbsafe)
