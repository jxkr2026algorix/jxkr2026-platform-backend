"""OSM 도로망을 라우팅 그래프로 읽는다.

입력은 osmium 이 내보낸 GeoJSON 또는 GeoJSONSeq 다.

    osmium extract -b 128.9,36.2,129.3,36.6 south-korea-latest.osm.pbf -o cheongsong.osm.pbf
    osmium tags-filter cheongsong.osm.pbf w/highway -o roads.osm.pbf
    osmium export roads.osm.pbf -f geojsonseq -o roads.geojsonseq

**이 파일을 저장소에 커밋하지 않는다.** OSM 파생물은 ODbL 이고, 커밋하면 그 데이터가
ODbL 이 된다. 경로는 `SALGIL_ROAD_NETWORK_PATH` 로 실행 시점에 주입한다.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import pickle
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger
from app.routing.profiles import ModeProfile

log = get_logger(__name__)

EARTH_RADIUS_M = 6_371_008.8

# 직렬화된 그래프의 형식 번호. RoadGraph 나 Edge 의 모양을 바꾸면 반드시 올린다.
# 안 올리면 옛 캐시가 새 코드로 읽혀 필드가 어긋난 채 동작한다.
_CACHE_VERSION = 1

# 좌표를 이 자리수로 반올림해 노드를 합친다. 7자리는 약 1cm 라 서로 다른 교차로가
# 합쳐지지 않고, 같은 교차로를 공유하는 way 들은 같은 노드가 된다.
_NODE_PRECISION = 7


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


NodeId = tuple[float, float]

# 공간 인덱스 셀 크기. 위도 0.005° 는 약 550m 로, 마을 단위 탐색에 적당하다.
_CELL_DEG = 0.005
_CELL_M = 550.0


def _cell(node: NodeId) -> tuple[int, int]:
    return (int(node[0] / _CELL_DEG), int(node[1] / _CELL_DEG))


@dataclass(frozen=True, slots=True)
class Edge:
    target: NodeId
    length_m: float
    way_id: str | None
    tags: dict[str, str]


@dataclass
class RoadGraph:
    """무방향 그래프.

    일방통행을 무시한다. 도보·자전거가 주 대상이고, 차량 대피에서도 재난 시
    역주행 통제가 이뤄지는 경우가 있어 일방통행을 절대 제약으로 두지 않았다.
    차량 경로를 실제 통제에 쓰려면 이 가정을 다시 봐야 한다.
    """

    nodes: dict[NodeId, list[Edge]] = field(default_factory=dict)
    source: str = ""
    way_count: int = 0
    attribution: str = "© OpenStreetMap contributors, ODbL 1.0"
    # 균일 격자 공간 인덱스. 셀 하나가 약 500m 다. 경북 전역 추출본이면 노드가
    # 백만 단위가 되어, 전수 탐색으로는 대피소 하나 찾는 데 초 단위가 걸린다.
    _index: dict[tuple[int, int], list[NodeId]] = field(default_factory=dict, repr=False)

    def __len__(self) -> int:
        return len(self.nodes)

    @property
    def edge_count(self) -> int:
        return sum(len(edges) for edges in self.nodes.values()) // 2

    def neighbours(self, node: NodeId) -> list[Edge]:
        return self.nodes.get(node, [])

    def add_segment(
        self,
        a: NodeId,
        b: NodeId,
        *,
        length_m: float,
        way_id: str | None,
        tags: dict[str, str],
    ) -> None:
        for node in (a, b):
            if node not in self.nodes:
                self.nodes[node] = []
                self._index.setdefault(_cell(node), []).append(node)
        self.nodes[a].append(Edge(b, length_m, way_id, tags))
        self.nodes[b].append(Edge(a, length_m, way_id, tags))

    def nearest_node(
        self, lat: float, lon: float, *, max_distance_m: float = 2000.0
    ) -> NodeId | None:
        """가장 가까운 노드. 너무 멀면 None 을 준다.

        멀리 있는 노드에 억지로 붙이면 마을에서 2km 떨어진 국도에서 출발하는
        경로가 나온다. 그건 경로가 아니라 그럴듯한 거짓말이다.
        """
        best: NodeId | None = None
        best_distance = max_distance_m

        # 반경을 넓혀 가며 인접 셀만 본다. 찾은 뒤에도 한 겹 더 보는 이유는,
        # 셀 경계 바로 바깥에 더 가까운 노드가 있을 수 있어서다.
        rings = max(1, int(max_distance_m / _CELL_M) + 1)
        origin = _cell((lat, lon))
        for radius in range(rings + 1):
            found_this_ring = False
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if radius and max(abs(dy), abs(dx)) != radius:
                        continue
                    for node in self._index.get((origin[0] + dy, origin[1] + dx), ()):
                        distance = haversine_m(lat, lon, node[0], node[1])
                        if distance < best_distance:
                            best, best_distance = node, distance
                            found_this_ring = True
            if best is not None and not found_this_ring and radius > 0:
                break
        return best


def _node_id(lon: float, lat: float) -> NodeId:
    return (round(lat, _NODE_PRECISION), round(lon, _NODE_PRECISION))


def _iter_features(path: Path) -> Iterator[dict]:
    """GeoJSON FeatureCollection 과 GeoJSONSeq 를 모두 읽는다."""
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("{") and '"FeatureCollection"' in stripped[:400]:
        payload = json.loads(text)
        yield from payload.get("features", [])
        return
    for line in text.splitlines():
        line = line.strip().lstrip("\x1e")  # geojsonseq 는 RS 문자로 구분한다
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _source_fingerprint(source: Path) -> str:
    """도로망 파일 내용의 해시.

    크기와 수정시각으로도 대개 맞지만, 같은 크기로 덮어써지는 경우를 놓친다.
    그때 나오는 것은 옛 지도로 계산한 대피 경로다. 160MB 를 읽는 데 1초가 채
    안 걸리고 그래프를 다시 만드는 데는 40초가 걸리므로, 확실한 쪽을 택한다.
    """
    digest = hashlib.blake2b(digest_size=16)
    with source.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cache_path(source: Path, profile: ModeProfile, fingerprint: str) -> Path:
    name = f"{fingerprint}-{profile.mode.value}-v{_CACHE_VERSION}.pickle"
    return source.parent / ".graph-cache" / name


def _read_cache(cache: Path, profile: ModeProfile) -> RoadGraph | None:
    if not cache.is_file():
        return None
    try:
        with cache.open("rb") as handle:
            graph = pickle.load(handle)
    except Exception as exc:
        # 캐시가 깨졌다고 경로 계산을 못 하게 둘 이유는 없다. 다시 만들면 된다.
        log.warning("road_graph_cache_unreadable", path=str(cache), error=str(exc))
        return None
    if not isinstance(graph, RoadGraph):
        log.warning("road_graph_cache_wrong_type", path=str(cache))
        return None
    log.info("road_graph_cache_hit", mode=profile.mode.value, nodes=len(graph), path=str(cache))
    return graph


def _write_cache(cache: Path, graph: RoadGraph) -> None:
    """같은 디렉터리에 임시 파일로 쓴 뒤 옮긴다.

    쓰는 도중에 죽으면 반쪽짜리 파일이 남고, 그것은 다음 기동에서 캐시로 읽힌다.
    rename 은 원자적이라 완성된 파일만 그 이름을 갖는다.
    """
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache.parent, delete=False) as tmp:
            pickle.dump(graph, tmp, protocol=pickle.HIGHEST_PROTOCOL)
            temporary = Path(tmp.name)
        os.replace(temporary, cache)
        log.info("road_graph_cached", path=str(cache), bytes=cache.stat().st_size)
    except OSError as exc:
        # 볼륨이 읽기 전용이면 캐시를 못 쓴다. 매번 다시 만들 뿐 동작에는 지장이 없다.
        log.warning("road_graph_cache_unwritable", path=str(cache), error=str(exc))


def load_road_graph(path: str | Path, profile: ModeProfile) -> RoadGraph:
    """교통수단 하나에 맞춰 그래프를 만든다.

    수단마다 통행 가능한 길이 다르므로 그래프도 수단별로 만든다. 하나의 그래프에
    전부 넣고 탐색 중에 거르면, 막힌 길을 지나야만 닿는 노드가 연결된 것처럼 보인다.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"도로망 파일이 없습니다: {source}")

    # 그래프 구축은 수단마다 수십 초가 걸리고, 원본이 그대로면 결과도 그대로다.
    # 만들어 둔 것이 있으면 그것을 쓴다.
    fingerprint = _source_fingerprint(source)
    cache = _cache_path(source, profile, fingerprint)
    cached = _read_cache(cache, profile)
    if cached is not None:
        return cached

    graph = RoadGraph(source=str(source))
    skipped: dict[str, int] = {}

    for feature in _iter_features(source):
        geometry = feature.get("geometry") or {}
        if geometry.get("type") != "LineString":
            continue
        tags = {str(k): str(v) for k, v in (feature.get("properties") or {}).items()}

        allowed, reason = profile.passable(tags)
        if not allowed:
            key = (reason or "unknown").split("=")[0]
            skipped[key] = skipped.get(key, 0) + 1
            continue

        coordinates = geometry.get("coordinates") or []
        if len(coordinates) < 2:
            continue

        way_id = str(feature.get("id") or tags.get("@id") or "") or None
        previous = _node_id(coordinates[0][0], coordinates[0][1])
        for point in coordinates[1:]:
            current = _node_id(point[0], point[1])
            if current == previous:
                continue
            graph.add_segment(
                previous,
                current,
                length_m=haversine_m(previous[0], previous[1], current[0], current[1]),
                way_id=way_id,
                tags=tags,
            )
            previous = current
        graph.way_count += 1

    log.info(
        "road_graph_loaded",
        mode=profile.mode.value,
        nodes=len(graph),
        edges=graph.edge_count,
        ways=graph.way_count,
        skipped=dict(sorted(skipped.items(), key=lambda kv: -kv[1])[:5]),
    )
    _write_cache(cache, graph)
    return graph
