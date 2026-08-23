"""대피 경로 서비스 — 도로망·예측·현장 보고를 하나의 경로로 묶는다.

세 가지가 들어온다.

- **도로망** OSM. ODbL 이라 응답에 출처를 싣는다
- **예측** 자체 모델. 시간에 따라 커지는 위험장을 만든다
- **현장 보고** 사람이 가서 본 통제 구간. 확률이 아니라 차단이다

셋의 성격이 다르므로 응답에서도 구분된다. 현장 보고가 예측을 이긴다 — 확인된 사실이
추정보다 강하다.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.gbsafe import GbSafeClient
from app.clients.mlengine import RECIPE_FOR_HAZARD, MlEngineClient
from app.core.config import Settings
from app.core.errors import NotFoundError, UpstreamError, ValidationError
from app.core.logging import get_logger
from app.db.models import Community, FieldReport, Shelter
from app.routing.graph import RoadGraph, haversine_m, load_road_graph
from app.routing.hazard import (
    BlockedPoint,
    HazardField,
    HazardPolicy,
    bbox_around,
    slice_from_grid,
    to_probability,
)
from app.routing.planner import plan_route
from app.routing.profiles import TransportMode, profile_for
from app.schemas.prediction import PredictionRequest
from app.schemas.routing import BlockedSegment, RouteLeg, RoutePlan, RouteRequest
from app.services import communities, situation

log = get_logger(__name__)

# 현장이 통제를 보고한 지점 주변 이 반경을 통행 불가로 본다. 보고에는 구간이 아니라
# 점 하나가 실리므로, 교량이나 유실 구간 하나를 덮을 만큼은 잡아야 한다.
FIELD_BLOCK_RADIUS_M = 120.0

# 예측 격자가 덮을 범위. 대피 반경보다 넉넉해야 경로가 격자 밖으로 나가지 않는다.
PREDICTION_RADIUS_M = 6000.0


@lru_cache(maxsize=8)
def _load_graph_cached(path: str, mode: str) -> RoadGraph:
    """도로망은 크고 잘 바뀌지 않는다. 수단별로 한 번만 읽는다."""
    return load_road_graph(path, profile_for(mode))


def road_network_available(settings: Settings) -> bool:
    return bool(settings.road_network_path)


# 수단별 잠금. lru_cache 는 같은 인자로 동시에 들어온 호출을 합쳐 주지 않아서,
# 콜드 상태에서 요청 둘이 겹치면 같은 그래프를 두 번 만들고 메모리도 두 배로 쓴다.
_graph_locks: dict[str, asyncio.Lock] = {}


async def get_graph(settings: Settings, mode: TransportMode) -> RoadGraph:
    """그래프를 스레드에서 읽는다.

    경북 전역 도로망은 만드는 데 수 분이 걸리는 순수 CPU 작업이다. 이벤트 루프
    위에서 하면 그동안 이 서비스의 **모든** 요청이 멈춘다 — 경로뿐 아니라 상황
    조회도, 헬스체크도. 실제로 컨테이너가 unhealthy 로 떨어졌다.
    """
    if not settings.road_network_path:
        raise ValidationError(
            "도로망이 설정되지 않았습니다 — SALGIL_ROAD_NETWORK_PATH 에 OSM 추출본을 "
            "지정하세요. 도로망 없이 경로를 만들면 지도 위에 그럴듯한 직선이 그려질 뿐입니다"
        )
    lock = _graph_locks.setdefault(mode.value, asyncio.Lock())
    async with lock:
        try:
            return await asyncio.to_thread(
                _load_graph_cached, settings.road_network_path, mode.value
            )
        except FileNotFoundError as exc:
            raise ValidationError(str(exc)) from exc


async def _field_blocks(session: AsyncSession, incident_id: uuid.UUID) -> list[BlockedPoint]:
    """현장 보고에서 확인된 통제 지점.

    좌표가 없는 보고는 쓸 수 없다 — 위치를 모르면 어느 길을 막아야 할지 알 수 없다.
    그런 보고를 조용히 버리지 않고 경고로 올린다.
    """
    rows = (
        (await session.execute(select(FieldReport).where(FieldReport.incident_id == incident_id)))
        .scalars()
        .all()
    )
    blocks: list[BlockedPoint] = []
    for report in rows:
        for constraint in report.access_constraints or []:
            lat, lon = constraint.get("lat"), constraint.get("lon")
            if lat is None or lon is None:
                continue
            blocks.append(
                BlockedPoint(
                    lat=float(lat),
                    lon=float(lon),
                    radius_m=FIELD_BLOCK_RADIUS_M,
                    kind=str(constraint.get("kind", "other")),
                    detail=constraint.get("detail") or constraint.get("location"),
                )
            )
    return blocks


async def _hazard_field(
    client: MlEngineClient,
    *,
    hazard: str,
    lat: float,
    lon: float,
    horizons: list[int],
    region_code: str | None,
    warnings: list[str],
) -> tuple[HazardField, dict]:
    """재난에 맞는 모델을 시점별로 돌려 시간축 위험장을 만든다."""
    recipe = RECIPE_FOR_HAZARD.get(hazard)
    if recipe is None:
        warnings.append(
            f"재난 '{hazard}' 에 대응하는 예측 모델이 없습니다 — 확산을 반영하지 못한 "
            "경로입니다. 현재 통제 정보만 반영됩니다"
        )
        return HazardField(), {}

    # 요청할 범위. 대피 반경보다 넉넉해야 경로가 격자 밖으로 나가지 않는다.
    assumed_bbox = bbox_around(lat, lon, PREDICTION_RADIUS_M)
    slices = []
    meta: dict = {}
    assumed_georeference = False

    # 덮어야 할 범위를 **요청에 실어** 보낸다. 그러면 응답의 격자가 어디에 놓이는지
    # 추측할 필요가 없다. 예전에는 여기서 6km 를 가정했고, 가정이 틀리면 위험 구역이
    # 실제와 다른 자리에 그려졌다.
    min_lat, min_lon, max_lat, max_lon = assumed_bbox
    requested_bbox = [min_lon, min_lat, max_lon, max_lat]

    for horizon in sorted(set(horizons)):
        request = PredictionRequest(
            recipe=recipe,
            region_code=region_code,
            hazard=hazard,
            horizon_minutes=horizon,
            bbox=requested_bbox,
            crs="EPSG:4326",
        )
        try:
            result = await client.predict(request)
        except (UpstreamError, ValidationError) as exc:
            # 예측 실패를 '위험 없음'으로 바꾸지 않는다. 경로는 내되 무엇을 반영하지
            # 못했는지 말한다.
            warnings.append(f"{horizon}분 예측을 받지 못했습니다: {exc.detail[:120]}")
            continue

        if not result.outputs or result.grid is None:
            warnings.append(f"{horizon}분 예측에 격자가 없어 반영하지 못했습니다")
            continue

        tensor = result.outputs[0]
        channels = len(result.summary.channels) or 1

        bbox, assumed = _grid_bbox(result.grid, assumed_bbox, warnings)
        if bbox is None:
            continue
        assumed_georeference = assumed_georeference or assumed

        try:
            slices.append(
                slice_from_grid(
                    horizon_minutes=horizon,
                    height=result.grid.height,
                    width=result.grid.width,
                    bbox=bbox,
                    values=to_probability(tensor.name, tensor.data),
                    channel=_hazard_channel(hazard, result),
                    channel_count=channels,
                )
            )
        except ValueError as exc:
            warnings.append(f"{horizon}분 예측 격자를 읽지 못했습니다: {exc}")
            continue

        meta = {
            "prediction_id": result.prediction_id,
            "model": f"{result.model.name}@{result.model.version or '?'}",
            "feature_mode": result.feature_mode.value,
            "is_stub": result.is_stub,
        }

    if assumed_georeference:
        warnings.append(
            "예측 응답에 좌표 범위(grid.bbox)가 없어 요청한 범위로 **가정**해 배치했습니다 "
            "— ML 서버가 요청의 bbox 를 응답에 되돌려주지 않고 있습니다. 모델이 실제로 "
            "덮는 범위가 다르면 위험이 엉뚱한 곳에 놓입니다"
        )

    feature_mode = meta.get("feature_mode")
    synthetic = feature_mode in ("synthetic", None) or bool(meta.get("is_stub"))

    if slices and synthetic:
        # **합성 입력으로 만든 위험장은 길을 막지 않는다.**
        #
        # 모델은 돌았지만 입력이 관측이 아니라 합성값이라, 그 출력은 불이 어디 있는지
        # 말해 주지 않는다. 그런 값으로 도로를 차단하면 "경로가 없습니다"라는 확신에 찬
        # 오답이 나간다 — 실제로는 '모른다'인데.
        #
        # 조회 실패를 '위험 없음'으로 읽으면 안 되는 것과 같은 이유로, 모르는 것을
        # '위험함'으로 읽어도 안 된다. 어느 쪽이든 관측하지 않은 것을 단정하는 일이다.
        #
        # 배포에서 이 값이 격자 중심(=출발지)에 봉우리를 만들어, 주민이 늘 불 한가운데
        # 서 있는 것으로 계산됐다. 모든 재난에서 대피 경로가 나오지 않았다.
        slices = []
        warnings.append(
            f"예측 입력이 합성값입니다 (feature_mode={feature_mode}) — 모델이 돌긴 했지만 "
            "그 출력은 불이 어디 있는지 말해 주지 않아, 확산을 경로에 반영하지 않았습니다. "
            "현장 보고의 통제 구간만 반영됩니다"
        )

    field = HazardField(
        slices=slices,
        hazard=hazard,
        model=meta.get("model"),
        feature_mode=feature_mode,
        is_stub=bool(meta.get("is_stub")),
    )
    return field, meta


def _grid_bbox(
    grid, assumed_bbox: tuple[float, float, float, float], warnings: list[str]
) -> tuple[tuple[float, float, float, float] | None, bool]:
    """예측 격자가 지도상 어디에 놓이는가.

    모델이 `grid.bbox` 를 주면 그걸 쓴다. 안 주면 출발지 중심으로 가정하되 **경고를
    남긴다.** 격자를 잘못 놓으면 위험 구역이 실제와 다른 자리에 그려지고, 경로는
    엉뚱한 길을 피해 간다. 조용히 가정하는 쪽이 못 쓰는 쪽보다 위험하다.

    재투영은 하지 않는다 — 좌표계가 WGS84 가 아니면 쓰지 않는다. KOGL 3·4 데이터는
    재투영 자체가 라이선스 위반이기도 하다.
    """
    if grid is None or not grid.bbox or len(grid.bbox) != 4:
        return assumed_bbox, True

    crs = (grid.crs or "").upper().replace(" ", "")
    if crs and crs not in {"EPSG:4326", "WGS84", "CRS84", "OGC:CRS84"}:
        warnings.append(
            f"예측 격자 좌표계가 {grid.crs} 입니다 — 재투영하지 않으므로 이 격자를 "
            "경로 계산에 쓰지 않았습니다"
        )
        return None, False

    # GridSpec.bbox 는 [minx, miny, maxx, maxy]. WGS84 에서 x 는 경도다.
    min_lon, min_lat, max_lon, max_lat = (float(v) for v in grid.bbox)
    return (min_lat, min_lon, max_lat, max_lon), False


def _hazard_channel(hazard: str, result) -> int | None:
    """여러 재난을 채널로 담는 모델에서 해당 재난의 채널 번호."""
    for summary in result.summary.channels:
        if summary.hazard == hazard:
            return summary.channel
    return None


async def _hazard_limitation(
    gbsafe: GbSafeClient | None, hazard: str, warnings: list[str]
) -> str | None:
    """이 재난에 '어디로 가라'를 말할 수 있는가.

    지진은 발생을 알려주지만 어느 대피소로 보낼지 모른다. 대피소가 없는 것과
    **애초에 갈 곳을 말할 수 없는 것**은 다르다 — 앞은 데이터 누락이고 뒤는 알려진
    한계다. 둘을 같은 404 로 묶으면 화면이 "계산 실패"라고만 말하고, 담당자는 없는
    데이터를 찾으러 간다.
    """
    if gbsafe is None:
        return None
    try:
        capability = await situation.capability_for(gbsafe, hazard)
    except UpstreamError as exc:
        warnings.append(f"재난 가용성을 확인하지 못했습니다: {exc.detail[:100]}")
        return None
    if capability is None or capability.can_say_where_to_go:
        return None
    return capability.caveat or (
        f"{capability.korean_name}은(는) 발생은 확인되지만 어느 대피소로 보낼지 "
        "말할 수 있는 공개 데이터가 없습니다 (가용성 partial)"
    )


async def plan_evacuation(
    session: AsyncSession,
    client: MlEngineClient,
    settings: Settings,
    payload: RouteRequest,
    gbsafe: GbSafeClient | None = None,
) -> RoutePlan:
    warnings: list[str] = []
    profile = profile_for(payload.mode)
    graph = await get_graph(settings, payload.mode)

    origin_community: Community | None = None
    if payload.community_id is not None:
        origin_community = await communities.get_community(session, payload.community_id)
        if origin_community.lat is None or origin_community.lon is None:
            raise ValidationError(
                f"마을 '{origin_community.name}' 에 좌표가 없습니다 — 경로를 계산할 수 없습니다"
            )
        lat, lon = origin_community.lat, origin_community.lon
        region_code = origin_community.region_code
    elif payload.lat is not None and payload.lon is not None:
        lat, lon, region_code = payload.lat, payload.lon, None
    else:
        raise ValidationError("community_id 또는 lat/lon 중 하나는 있어야 합니다")

    # 대피소는 재난별로 다르다. 지진 대피소를 산불 대피소로 자동 전용하지 않는다.
    candidates = await communities.find_shelters(
        session,
        hazard=payload.hazard,
        region_code=region_code,
        lat=lat,
        lon=lon,
        limit=payload.max_shelters,
    )
    if payload.shelter_id is not None:
        shelter = await session.get(Shelter, payload.shelter_id)
        if shelter is None:
            raise NotFoundError(f"대피소 {payload.shelter_id} 를 찾지 못했습니다")
        if payload.hazard not in (shelter.hazards or []):
            raise ValidationError(
                f"대피소 '{shelter.name}' 은 {payload.hazard} 를 담당하지 않습니다 — "
                "다른 재난의 대피소를 전용하면 안 됩니다"
            )
        distance = (
            round(haversine_m(lat, lon, shelter.lat, shelter.lon) / 1000, 3)
            if shelter.lat is not None and shelter.lon is not None
            else None
        )
        candidates = [(shelter, distance)]

    limitation = await _hazard_limitation(gbsafe, payload.hazard, warnings)

    if not candidates:
        if limitation is not None:
            # 계산 실패가 아니라 알려진 한계다. 200 으로 돌려주되 경로가 없다는 것과
            # 그 이유를 분명히 한다 — 화면이 "가까운 대피소가 없다"로 읽으면 안 된다.
            return RoutePlan(
                origin={
                    "lat": lat,
                    "lon": lon,
                    "community_id": str(origin_community.id) if origin_community else None,
                    "community_name": origin_community.name if origin_community else None,
                },
                hazard=payload.hazard,
                mode=payload.mode,
                mode_name=profile.korean_name,
                mode_note=profile.note or None,
                routes=[],
                recommended=None,
                shelter_guidance_available=False,
                hazard_limitation=limitation,
                road_network=graph.source,
                warnings=[*warnings, limitation],
                generated_at=datetime.now(UTC),
            )
        raise NotFoundError(
            f"{payload.hazard} 에 쓸 수 있는 대피소가 없습니다 — "
            "대피소가 등록되지 않았거나 이 재난을 담당하는 시설이 없습니다"
        )

    hazard_field = HazardField()
    meta: dict = {}
    if payload.use_prediction:
        hazard_field, meta = await _hazard_field(
            client,
            hazard=payload.hazard,
            lat=lat,
            lon=lon,
            horizons=payload.horizons_minutes,
            region_code=region_code,
            warnings=warnings,
        )
    else:
        warnings.append("예측을 쓰지 않았습니다 — 확산이 반영되지 않은 경로입니다")

    if payload.incident_id is not None:
        hazard_field.blocked = await _field_blocks(session, payload.incident_id)

    policy = HazardPolicy(
        block_threshold=payload.block_threshold,
        avoid_threshold=min(payload.avoid_threshold, payload.block_threshold),
    )

    legs: list[RouteLeg] = []
    for shelter, straight_km in candidates:
        if shelter.lat is None or shelter.lon is None:
            legs.append(
                RouteLeg(
                    shelter_id=shelter.id,
                    shelter_name=shelter.name,
                    found=False,
                    reason="대피소에 좌표가 없습니다",
                )
            )
            continue

        result = plan_route(
            graph,
            profile,
            hazard_field,
            policy,
            origin=(lat, lon),
            destination=(shelter.lat, shelter.lon),
            depart_after_s=payload.depart_after_minutes * 60.0,
        )
        legs.append(
            RouteLeg(
                shelter_id=shelter.id,
                shelter_name=shelter.name,
                shelter_capacity=shelter.capacity,
                capacity_basis=shelter.capacity_basis,
                found=result.found,
                reason=result.reason,
                geometry=[[lon_, lat_] for lon_, lat_ in result.coordinates],
                distance_m=result.distance_m if result.found else None,
                duration_minutes=result.duration_minutes if result.found else None,
                straight_line_km=straight_km,
                max_risk=result.max_risk if result.found else None,
                mean_risk=result.mean_risk if result.found else None,
                avoided_edges=result.avoided_edges,
                blocked_by_reports=[
                    BlockedSegment(
                        kind=point.kind,
                        lat=point.lat,
                        lon=point.lon,
                        radius_m=point.radius_m,
                        detail=point.detail,
                    )
                    for point in result.blocked_by_reports
                ],
            )
        )

    legs.sort(key=lambda leg: (not leg.found, leg.duration_minutes or float("inf")))
    recommended = legs[0].shelter_id if legs and legs[0].found else None
    if recommended is None:
        warnings.append(
            "모든 후보 대피소로 가는 경로를 찾지 못했습니다 — 각 항목의 reason 을 확인하세요"
        )

    return RoutePlan(
        origin={
            "lat": lat,
            "lon": lon,
            "community_id": str(origin_community.id) if origin_community else None,
            "community_name": origin_community.name if origin_community else None,
        },
        hazard=payload.hazard,
        mode=payload.mode,
        mode_name=profile.korean_name,
        mode_note=profile.note or None,
        shelter_guidance_available=limitation is None,
        hazard_limitation=limitation,
        routes=legs,
        recommended=recommended,
        prediction_used=hazard_field.has_prediction,
        prediction_id=meta.get("prediction_id"),
        prediction_model=meta.get("model"),
        feature_mode=meta.get("feature_mode"),
        prediction_is_stub=bool(meta.get("is_stub")),
        horizons_minutes=hazard_field.horizons,
        field_reports_applied=len(hazard_field.blocked),
        road_network=graph.source,
        warnings=warnings,
        generated_at=datetime.now(UTC),
    )
