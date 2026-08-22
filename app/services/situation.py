"""상황판 서비스 — 상류 봉투에 가용성과 3상태를 붙여 화면 한 장으로 만든다."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.clients.gbsafe import GbSafeClient
from app.core.errors import UpstreamError
from app.core.logging import get_logger
from app.schemas.common import DataState, Envelope
from app.schemas.hazard import Hazard, HazardCapability, Readiness, korean_name, map_scenario
from app.schemas.meta import CapabilityMatrix, Region, ResolvedRegion
from app.schemas.situation import HazardSnapshot, SituationContext, SituationOverview

log = get_logger(__name__)

# 상황판 첫 화면이 훑는 재난. 세 축이 다 있는 것만 둔다 —
# partial 을 첫 화면에 ready 처럼 늘어놓으면 갈 곳 없는 안내가 나간다.
OVERVIEW_HAZARDS: tuple[Hazard, ...] = (
    Hazard.HEAVY_RAIN,
    Hazard.FLOOD,
    Hazard.LANDSLIDE,
    Hazard.WILDFIRE,
    Hazard.TYPHOON,
)


async def capabilities(client: GbSafeClient) -> CapabilityMatrix:
    payload = await client.capabilities()
    hazards: list[HazardCapability] = []
    for raw in payload.get("hazards", []):
        item = HazardCapability.model_validate(raw)
        item.map_scenario = map_scenario(item.hazard)
        hazards.append(item)

    return CapabilityMatrix(
        hazards=hazards,
        ready=[h.hazard for h in hazards if h.readiness is Readiness.READY],
        partial=[h.hazard for h in hazards if h.readiness is Readiness.PARTIAL],
        blocked=[h.hazard for h in hazards if h.readiness is Readiness.BLOCKED],
        fetched_at=datetime.now(UTC),
    )


async def capability_for(client: GbSafeClient, hazard: str) -> HazardCapability | None:
    matrix = await capabilities(client)
    for item in matrix.hazards:
        if item.hazard == hazard:
            return item
    return None


async def regions(client: GbSafeClient) -> list[Region]:
    payload = await client.regions()
    return [Region.model_validate(item) for item in payload.get("regions", [])]


async def resolve_region(client: GbSafeClient, query: str) -> ResolvedRegion:
    payload = await client.resolve_region(query)
    return ResolvedRegion.model_validate(payload)


def headline_caveat(envelope: Envelope, capability: HazardCapability | None) -> str | None:
    """화면 상단에 그대로 띄울 한 줄.

    우선순위가 있다. 못 읽은 원천이 있으면 그게 먼저다 — 오래된 값보다 위험한 상태라서다.
    """
    if envelope.failed_sources:
        detail = next(
            (d.detail for d in envelope.degradations if d.detail),
            None,
        )
        names = ", ".join(envelope.failed_sources)
        return (
            f"원천 {names} 을(를) 읽지 못했습니다 — 이 화면을 '위험 없음'으로 읽으면 안 됩니다."
            + (f" ({detail[:120]})" if detail else "")
        )
    if capability is not None and capability.caveat:
        return capability.caveat
    if envelope.has_stale_records:
        return "일부 값이 갱신주기를 넘겼습니다 — 관측 시각을 함께 확인하세요."
    if envelope.caveats:
        return envelope.caveats[0]
    return None


async def context(
    client: GbSafeClient, *, region_query: str, hazard: str | None = None
) -> SituationContext:
    resolved, capability = await asyncio.gather(
        resolve_region(client, region_query),
        capability_for(client, hazard) if hazard else _none(),
    )

    region_name = resolved.name or region_query
    envelope = await client.hazard_context(region_name, hazard)

    return SituationContext(
        region=resolved,
        hazard=hazard,
        hazard_korean=korean_name(hazard) if hazard else None,
        capability=capability,
        envelope=envelope,
        state=envelope.state,
        headline_caveat=headline_caveat(envelope, capability),
        fetched_at=datetime.now(UTC),
    )


async def _none() -> None:
    return None


async def overview(
    client: GbSafeClient, *, region_query: str, open_incidents: int = 0
) -> SituationOverview:
    """콘솔 첫 화면 묶음.

    재난 하나가 실패해도 나머지를 돌려준다. 다만 실패를 **조용히** 지우지 않는다 —
    그 재난은 `state=UNVERIFIED` 로 남고 `unverified_sources` 에 사유가 실린다.
    """
    resolved = await resolve_region(client, region_query)
    region_name = resolved.name or region_query

    matrix = await capabilities(client)
    by_hazard = {item.hazard: item for item in matrix.hazards}

    async def one(hazard: Hazard) -> HazardSnapshot:
        capability = by_hazard.get(hazard.value)
        base = HazardSnapshot(
            hazard=hazard.value,
            hazard_korean=korean_name(hazard),
            map_scenario=map_scenario(hazard),
            readiness=capability.readiness.value if capability else "blocked",
            state=DataState.UNVERIFIED,
            caveat=capability.caveat if capability else None,
        )
        try:
            envelope = await client.hazard_context(region_name, hazard.value)
        except UpstreamError as exc:
            log.warning("overview_hazard_failed", hazard=hazard.value, detail=exc.detail)
            base.caveat = f"조회 실패: {exc.detail[:160]}"
            base.failed_sources = ["gbsafedata"]
            return base

        base.state = envelope.state
        base.record_count = len(envelope.records)
        base.complete = envelope.complete
        base.absence_confirmed = envelope.absence_confirmed
        base.failed_sources = envelope.failed_sources
        base.caveat = headline_caveat(envelope, capability)
        return base

    snapshots = await asyncio.gather(*(one(h) for h in OVERVIEW_HAZARDS))

    unverified: list[str] = []
    for snapshot in snapshots:
        unverified.extend(snapshot.failed_sources)

    return SituationOverview(
        region=resolved,
        generated_at=datetime.now(UTC),
        hazards=list(snapshots),
        open_incidents=open_incidents,
        unverified_sources=sorted(set(unverified)),
    )
