"""메타 — 지역, 재난 가용성, 데이터셋 검증."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import CurrentPrincipal, GbSafe
from app.api.route import TransactionalRoute
from app.schemas.hazard import Hazard, korean_name, map_scenario
from app.schemas.meta import CapabilityMatrix, Region, ResolvedRegion
from app.services import situation

router = APIRouter(prefix="/meta", tags=["meta"], route_class=TransactionalRoute)


@router.get("/regions", response_model=list[Region], summary="경북 시군 22개")
async def list_regions(client: GbSafe, _: CurrentPrincipal) -> list[Region]:
    return await situation.regions(client)


@router.get(
    "/regions/resolve",
    response_model=ResolvedRegion,
    summary="지역명 → 코드·좌표·기상격자",
)
async def resolve_region(
    client: GbSafe,
    _: CurrentPrincipal,
    q: str = Query(description="시군명, 시군구 코드, 또는 '문경시 산북면'"),
) -> ResolvedRegion:
    return await situation.resolve_region(client, q)


@router.get(
    "/hazards",
    response_model=CapabilityMatrix,
    summary="재난 13종 가용성 — ready / partial / blocked",
    description=(
        "세 축(탐지·위험도·대피소) 기준 가용성. **partial 을 ready 처럼 그리면 안 된다.** "
        "지진은 발생을 알려주지만 어느 대피소로 보낼지 모른다."
    ),
)
async def hazard_capabilities(client: GbSafe, _: CurrentPrincipal) -> CapabilityMatrix:
    return await situation.capabilities(client)


@router.get(
    "/hazards/map-scenarios",
    summary="프론트엔드 맵 시나리오명 ↔ 정규 재난 코드",
    description="map-webgpu-canvas 의 scenario 값과 GB SafeData 의 hazard 값을 잇는 표.",
)
async def map_scenarios(_: CurrentPrincipal) -> dict[str, Any]:
    return {
        "hazards": [
            {
                "hazard": hazard.value,
                "korean_name": korean_name(hazard),
                "map_scenario": map_scenario(hazard),
            }
            for hazard in Hazard
        ]
    }


@router.get("/datasets", summary="데이터셋 검색 (상류 프록시)")
async def search_datasets(
    client: GbSafe,
    _: CurrentPrincipal,
    q: str | None = None,
    hazard: str | None = None,
    dev_ready_only: bool | None = None,
    usable_only: bool | None = None,
    must_allow: str | None = Query(
        default=None, description="read | derive | redistribute | commercial"
    ),
    limit: int | None = None,
) -> Any:
    return await client.datasets(
        q=q,
        hazard=hazard,
        dev_ready_only=dev_ready_only,
        usable_only=usable_only,
        must_allow=must_allow,
        limit=limit,
    )


@router.get(
    "/datasets/{dataset_id}/verify",
    summary="이 용도로 써도 되는지 판정",
    description=(
        "라이선스와 심의 상태를 함께 본다. 재투영·클리핑·조인·파생라벨은 `derive` 이고 "
        "KOGL 3·4(변경금지)에서 막힌다."
    ),
)
async def verify_dataset(
    dataset_id: str,
    client: GbSafe,
    _: CurrentPrincipal,
    operation: str = Query(default="read"),
) -> Any:
    return await client.verify_dataset(dataset_id, operation)


@router.get("/datasets/{dataset_id}/citation", summary="출처 표기 문구")
async def dataset_citation(dataset_id: str, client: GbSafe, _: CurrentPrincipal) -> Any:
    return await client.dataset_citation(dataset_id)


@router.get("/quality", summary="검증으로 확인된 데이터 품질 결함")
async def quality(client: GbSafe, _: CurrentPrincipal) -> Any:
    return await client.quality()
