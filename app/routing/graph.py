"""OSM 도로망을 라우팅 그래프로 읽는다.

입력은 osmium 이 내보낸 GeoJSON 또는 GeoJSONSeq 다.

    osmium extract -b 128.9,36.2,129.3,36.6 south-korea-latest.osm.pbf -o cheongsong.osm.pbf
    osmium tags-filter cheongsong.osm.pbf w/highway -o roads.osm.pbf
    osmium export roads.osm.pbf -f geojsonseq -o roads.geojsonseq

**이 파일을 저장소에 커밋하지 않는다.** OSM 파생물은 ODbL 이고, 커밋하면 그 데이터가
ODbL 이 된다. 경로는 `SALGIL_ROAD_NETWORK_PATH` 로 실행 시점에 주입한다.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import get_logger
from app.routing.profiles import ModeProfile

log = get_logger(__name__)

EARTH_RADIUS_M = 6_371_008.8

# 좌표를 이 자리수로 반올림해 노드를 합친다. 7자리는 약 1cm 라 서로 다른 교차로가
# 합쳐지지 않고, 같은 교차로를 공유하는 way 들은 같은 노드가 된다.
_NODE_PRECISION = 7

# 형상점을 지우고 남기는 최소 간격. OSM 은 곡선을 10~20m 간격 점으로 표현하는데,
# 그 점들은 교차로가 아니라서 경로 탐색에 아무 정보를 주지 않으면서 노드 수만
# 늘린다. 경북 전역이면 그 때문에 노드가 357만 개가 되고, 파이썬 객체 1500만 개를
# 만드느라 그래프 하나에 50초가 걸린다.
#
# 다만 전부 지우면 안 된다. 출발지는 가장 가까운 노드에 붙는데, 남은 노드가 교차로
# 뿐이면 시골 도로에서 수 km 떨어진 곳에서 출발하는 경로가 나온다. 그건 경로가
# 아니라 그럴듯한 거짓말이다. 그래서 간격을 두고 남겨 스냅 오차를 이 값의 절반으로
# 묶는다.
_MIN_NODE_SPACING_M = 150.0


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


def _contract(graph: RoadGraph, spacing_m: float = _MIN_NODE_SPACING_M) -> RoadGraph:
    """형상점을 걷어내고 교차로와 일정 간격의 점만 남긴다.

    degree 가 2 인 노드는 지나가는 길목일 뿐이라 경로 선택지를 만들지 않는다.
    이어진 두 엣지를 길이를 더한 하나로 합치면 탐색 결과는 그대로면서 노드가
    한 자리수 줄어든다.
    """
    junctions = {node for node, edges in graph.nodes.items() if len(edges) != 2}

    # 교차로가 하나도 없는 고리(로터리, 순환도로)는 시작점이 없다. 임의의 한 점을
    # 교차로로 삼아야 그 고리가 통째로 사라지지 않는다.
    seen_in_chain: set[NodeId] = set()
    for node in graph.nodes:
        if node in junctions or node in seen_in_chain:
            continue
        ring: list[NodeId] = []
        current, previous = node, None
        while current not in junctions and current not in ring:
            ring.append(current)
            nxt = [e.target for e in graph.nodes[current] if e.target != previous]
            if not nxt:
                break
            previous, current = current, nxt[0]
        if current == node:  # 한 바퀴 돌아 제자리 — 고리다
            junctions.add(node)
        seen_in_chain.update(ring)

    contracted = RoadGraph(
        source=graph.source, way_count=graph.way_count, attribution=graph.attribution
    )
    walked: set[tuple[NodeId, NodeId]] = set()

    for start in junctions:
        for first in graph.nodes[start]:
            if (start, first.target) in walked:
                continue
            anchor = start
            previous, current = start, first.target
            length = first.length_m
            # 양방향 모두 표시한다. 한쪽만 표시하면 반대편 교차로에서 같은 체인을
            # 다시 걸어 엣지가 두 벌 생긴다.
            walked.add((start, first.target))
            walked.add((first.target, start))

            while True:
                edges = graph.nodes[current]
                at_junction = current in junctions
                # 간격을 넘겼으면 여기서 한 번 끊어 노드를 남긴다.
                if at_junction or length >= spacing_m:
                    contracted.add_segment(
                        anchor, current, length_m=length, way_id=first.way_id, tags=first.tags
                    )
                    if at_junction:
                        break
                    anchor, length = current, 0.0

                nxt = next((e for e in edges if e.target != previous), None)
                if nxt is None:  # 막다른 길
                    if current != anchor:
                        contracted.add_segment(
                            anchor, current, length_m=length, way_id=first.way_id, tags=first.tags
                        )
                    break
                walked.add((current, nxt.target))
                walked.add((nxt.target, current))
                previous, current = current, nxt.target
                length += nxt.length_m

    log.info(
        "road_graph_contracted",
        nodes_before=len(graph),
        nodes_after=len(contracted),
        edges_after=contracted.edge_count,
        spacing_m=spacing_m,
    )
    return contracted


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


def load_road_graph(path: str | Path, profile: ModeProfile) -> RoadGraph:
    """교통수단 하나에 맞춰 그래프를 만든다.

    수단마다 통행 가능한 길이 다르므로 그래프도 수단별로 만든다. 하나의 그래프에
    전부 넣고 탐색 중에 거르면, 막힌 길을 지나야만 닿는 노드가 연결된 것처럼 보인다.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"도로망 파일이 없습니다: {source}")

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
    return _contract(graph)
