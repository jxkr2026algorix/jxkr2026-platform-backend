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
from app.schemas.situation import (
    HazardSnapshot,
    SituationContext,
    SituationOverview,
    WeatherReading,
    WeatherSnapshot,
)

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


# 화면이 바로 쓰는 값들. 나머지 관측도 readings 로 함께 나간다.
_HEADLINE_KINDS = {
    "temperature": "temperature_c",
    "humidity": "humidity_pct",
    "wind_speed": "wind_speed_ms",
    "wind_direction": "wind_direction_deg",
    "rainfall_1h": "rainfall_1h_mm",
}


async def weather(client: GbSafeClient, *, region_query: str) -> WeatherSnapshot:
    """시군 하나의 현재 기상.

    `weather_now` 커넥터의 봉투를 화면이 쓰기 좋게 추린다. 값이 없으면 지어내지 않고
    `state` 로 말한다 — 하드코딩된 데모 값을 대체하려고 만든 것이라, 여기서 다시
    그럴듯한 기본값을 채우면 만든 이유가 없어진다.
    """
    resolved = await resolve_region(client, region_query)
    region_name = resolved.name or region_query
    envelope = await client.source("weather_now", region=region_name)

    readings: list[WeatherReading] = []
    headline: dict[str, float] = {}
    observed: datetime | None = None
    stale = False

    for record in envelope.records:
        payload = record.payload or {}
        kind = str(payload.get("kind", "")) or None
        if kind is None:
            continue
        freshness = record.freshness
        record_stale = bool(freshness and freshness.status == "stale")
        stale = stale or record_stale

        value = payload.get("value")
        try:
            numeric = float(value) if value is not None else None
        except (TypeError, ValueError):
            numeric = None

        readings.append(
            WeatherReading(
                kind=kind,
                value=numeric,
                unit=payload.get("unit"),
                station=payload.get("station"),
                observed_at=freshness.as_of if freshness else None,
                is_forecast=bool(payload.get("is_forecast")),
                stale=record_stale,
            )
        )
        if kind in _HEADLINE_KINDS and numeric is not None:
            headline[_HEADLINE_KINDS[kind]] = numeric
        if freshness and freshness.as_of and (observed is None or freshness.as_of > observed):
            observed = freshness.as_of

    citation = envelope.citations[0] if envelope.citations else None
    source = envelope.records[0].source if envelope.records else None

    return WeatherSnapshot(
        region=resolved,
        state=envelope.state,
        readings=readings,
        observed_at=observed,
        stale=stale,
        caveats=envelope.caveats,
        attribution=(citation.text if citation else None)
        or (source.attribution if source else None),
        source_url=source.source_url if source else None,
        fetched_at=datetime.now(UTC),
        **headline,
    )


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
