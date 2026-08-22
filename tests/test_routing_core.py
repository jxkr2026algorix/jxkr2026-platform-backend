"""경로 계산 코어 — 위험을 실제로 피하는가, 못 피하면 그렇다고 말하는가."""

from __future__ import annotations

import json
from itertools import pairwise

import pytest

from app.routing.graph import haversine_m, load_road_graph
from app.routing.hazard import HazardField, HazardPolicy, slice_from_grid
from app.routing.planner import plan_route
from app.routing.profiles import TransportMode, profile_for

# 청송 부근에 3×3 격자 도로망을 만든다. 위도 0.002 ≈ 222m.
BASE_LAT, BASE_LON = 36.40, 129.15
STEP = 0.002


def _grid_geojson(tags: dict | None = None) -> dict:
    tags = tags or {"highway": "residential"}
    features = []
    for row in range(3):
        for col in range(3):
            lat = BASE_LAT + row * STEP
            lon = BASE_LON + col * STEP
            if col < 2:
                features.append(_line(lat, lon, lat, lon + STEP, tags, f"h{row}{col}"))
            if row < 2:
                features.append(_line(lat, lon, lat + STEP, lon, tags, f"v{row}{col}"))
    return {"type": "FeatureCollection", "features": features}


def _line(lat1, lon1, lat2, lon2, tags, ident):
    return {
        "type": "Feature",
        "id": ident,
        "properties": dict(tags),
        "geometry": {"type": "LineString", "coordinates": [[lon1, lat1], [lon2, lat2]]},
    }


@pytest.fixture
def road_file(tmp_path):
    path = tmp_path / "roads.geojson"
    path.write_text(json.dumps(_grid_geojson()), encoding="utf-8")
    return path


@pytest.fixture
def graph(road_file):
    return load_road_graph(road_file, profile_for(TransportMode.FOOT))


def _hazard(values_by_horizon: dict[int, list[float]], height=3, width=3) -> HazardField:
    bbox = (BASE_LAT, BASE_LON, BASE_LAT + 2 * STEP, BASE_LON + 2 * STEP)
    return HazardField(
        slices=[
            slice_from_grid(
                horizon_minutes=horizon, height=height, width=width, bbox=bbox, values=values
            )
            for horizon, values in values_by_horizon.items()
        ]
    )


def test_graph_is_connected(graph):
    assert len(graph) == 9
    assert graph.edge_count == 12


def test_route_found_without_hazard(graph):
    result = plan_route(
        graph,
        profile_for(TransportMode.FOOT),
        HazardField(),
        HazardPolicy(),
        origin=(BASE_LAT, BASE_LON),
        destination=(BASE_LAT + 2 * STEP, BASE_LON + 2 * STEP),
    )
    assert result.found
    assert result.distance_m > 0
    assert result.coordinates[0] == pytest.approx((BASE_LON, BASE_LAT))


def test_route_avoids_a_dangerous_cell(graph):
    """가운데 열이 위험하면 그 열을 지나지 않아야 한다."""
    # 행 0 이 북쪽이다. 가운데(1,1) 칸만 위험.
    values = [0.0, 0.0, 0.0, 0.0, 0.9, 0.0, 0.0, 0.0, 0.0]
    result = plan_route(
        graph,
        profile_for(TransportMode.FOOT),
        _hazard({0: values}),
        HazardPolicy(block_threshold=0.5),
        origin=(BASE_LAT, BASE_LON),
        destination=(BASE_LAT + 2 * STEP, BASE_LON + 2 * STEP),
    )
    assert result.found
    assert result.avoided_edges > 0
    centre = (BASE_LON + STEP, BASE_LAT + STEP)
    assert all(
        not (abs(lon - centre[0]) < 1e-9 and abs(lat - centre[1]) < 1e-9)
        for lon, lat in result.coordinates
    )
    assert result.max_risk < 0.5


def test_spreading_hazard_closes_a_route_that_was_open_at_departure(graph):
    """출발 시점에는 안전하지만 도착할 무렵 번지는 경우.

    시간을 무시하면 이 경로를 안전하다고 내준다. 그게 이 기능이 막으려는 것이다.
    """
    safe_now = [0.0] * 9
    burning_later = [0.0, 0.0, 0.0, 0.0, 0.95, 0.0, 0.0, 0.0, 0.0]

    # 도보 1.1m/s 로 한 칸(222m)에 약 200초. 60분 뒤에는 이미 번져 있다.
    field = _hazard({0: safe_now, 60: burning_later})
    at_start = field.risk_at(BASE_LAT + STEP, BASE_LON + STEP, 0.0)
    later = field.risk_at(BASE_LAT + STEP, BASE_LON + STEP, 60 * 60)
    assert at_start == 0.0
    assert later >= 0.95


def test_hazard_is_monotone_in_time(graph):
    """한 번 위험해진 칸은 계속 위험하다 — 불이 지나간 자리를 안전으로 읽지 않는다."""
    field = _hazard({30: [0.9] * 9, 60: [0.0] * 9})
    assert field.risk_at(BASE_LAT, BASE_LON, 30 * 60) == pytest.approx(0.9)
    # 60분 시점의 예측이 0 이어도, 30분에 위험했으므로 여전히 위험하다
    assert field.risk_at(BASE_LAT, BASE_LON, 60 * 60) == pytest.approx(0.9)


def test_no_route_says_why_instead_of_returning_empty(graph):
    """전부 막히면 빈 경로가 아니라 사유를 준다."""
    result = plan_route(
        graph,
        profile_for(TransportMode.FOOT),
        _hazard({0: [0.99] * 9}),
        HazardPolicy(block_threshold=0.5),
        origin=(BASE_LAT, BASE_LON),
        destination=(BASE_LAT + 2 * STEP, BASE_LON + 2 * STEP),
    )
    assert not result.found
    assert result.coordinates == []
    assert "위험 구역을 피해서는" in result.reason
    assert result.avoided_edges > 0


def test_disconnected_is_distinguished_from_blocked(tmp_path):
    """'길이 없다'와 '위험해서 막혔다'는 다른 사유여야 한다."""
    payload = {
        "type": "FeatureCollection",
        "features": [
            _line(BASE_LAT, BASE_LON, BASE_LAT, BASE_LON + STEP, {"highway": "residential"}, "a"),
            _line(
                BASE_LAT + 5 * STEP,
                BASE_LON + 5 * STEP,
                BASE_LAT + 5 * STEP,
                BASE_LON + 6 * STEP,
                {"highway": "residential"},
                "b",
            ),
        ],
    }
    path = tmp_path / "split.geojson"
    path.write_text(json.dumps(payload), encoding="utf-8")
    graph = load_road_graph(path, profile_for(TransportMode.FOOT))

    result = plan_route(
        graph,
        profile_for(TransportMode.FOOT),
        HazardField(),
        HazardPolicy(),
        origin=(BASE_LAT, BASE_LON),
        destination=(BASE_LAT + 5 * STEP, BASE_LON + 6 * STEP),
    )
    assert not result.found
    assert "이어지지 않습니다" in result.reason
    assert result.avoided_edges == 0


def test_field_report_blocks_harder_than_prediction(graph):
    """현장 보고는 확률이 아니라 차단이다."""
    from app.routing.hazard import BlockedPoint

    field = HazardField(
        blocked=[
            BlockedPoint(
                lat=BASE_LAT + STEP,
                lon=BASE_LON + STEP,
                radius_m=80.0,
                kind="bridge_unsafe",
                detail="부남교 유실",
            )
        ]
    )
    result = plan_route(
        graph,
        profile_for(TransportMode.FOOT),
        field,
        HazardPolicy(),
        origin=(BASE_LAT, BASE_LON),
        destination=(BASE_LAT + 2 * STEP, BASE_LON + 2 * STEP),
    )
    assert result.found
    assert result.blocked_by_reports
    assert result.blocked_by_reports[0].kind == "bridge_unsafe"


def test_departure_delay_increases_exposure(graph):
    """출발이 늦어지면 그만큼 위험이 커진 상태로 판단한다."""
    field = _hazard({0: [0.0] * 9, 30: [0.9] * 9})
    policy = HazardPolicy(block_threshold=0.5)
    kwargs = dict(
        origin=(BASE_LAT, BASE_LON),
        destination=(BASE_LAT + 2 * STEP, BASE_LON + 2 * STEP),
    )
    immediate = plan_route(
        graph, profile_for(TransportMode.FOOT), field, policy, depart_after_s=0.0, **kwargs
    )
    delayed = plan_route(
        graph, profile_for(TransportMode.FOOT), field, policy, depart_after_s=40 * 60, **kwargs
    )
    assert immediate.found
    assert not delayed.found


def test_straight_line_distance_sanity():
    assert haversine_m(BASE_LAT, BASE_LON, BASE_LAT + STEP, BASE_LON) == pytest.approx(222, abs=5)


class TestContraction:
    """형상점을 걷어내 노드 수를 줄인다. 줄이는 것 자체는 목적이 아니고, 경로
    결과가 같아야 의미가 있다."""

    @staticmethod
    def _chain_geojson(points: int, step_deg: float) -> dict:
        """교차로 없이 점만 이어진 긴 길 하나."""
        coords = [[BASE_LON + i * step_deg, BASE_LAT] for i in range(points)]
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "way/1",
                    "properties": {"highway": "residential"},
                    "geometry": {"type": "LineString", "coordinates": coords},
                }
            ],
        }

    def _load(self, tmp_path, payload):
        path = tmp_path / "roads.geojson"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_road_graph(path, profile_for(TransportMode.FOOT))

    def test_dense_shape_points_collapse(self, tmp_path):
        """10m 간격 점 200개짜리 직선은 교차로가 없다. 전부 남길 이유가 없다."""
        graph = self._load(tmp_path, self._chain_geojson(200, 0.0001))
        assert len(graph) < 30, f"형상점이 그대로 남았습니다 ({len(graph)}개)"

    def test_total_length_is_preserved(self, tmp_path):
        """합쳐진 엣지의 길이 합은 원래 길과 같아야 한다. 여기서 어긋나면
        대피 경로의 거리와 소요시간이 전부 틀어진다."""
        payload = self._chain_geojson(200, 0.0001)
        graph = self._load(tmp_path, payload)

        coords = payload["features"][0]["geometry"]["coordinates"]
        expected = sum(
            haversine_m(a[1], a[0], b[1], b[0]) for a, b in pairwise(coords)
        )
        total = sum(e.length_m for edges in graph.nodes.values() for e in edges) / 2
        assert total == pytest.approx(expected, rel=0.001)

    def test_snapping_stays_close(self, tmp_path):
        """축약해도 길 위의 점은 가까운 노드를 찾아야 한다. 교차로만 남기면
        시골 도로에서 수 km 떨어진 곳에서 출발하는 경로가 나온다."""
        graph = self._load(tmp_path, self._chain_geojson(200, 0.0001))

        coords = self._chain_geojson(200, 0.0001)["features"][0]["geometry"]["coordinates"]
        worst = 0.0
        for lon, lat in coords:
            node = graph.nearest_node(lat, lon, max_distance_m=2000.0)
            assert node is not None, "길 위의 점인데 노드를 못 찾았습니다"
            worst = max(worst, haversine_m(lat, lon, node[0], node[1]))
        assert worst < 100.0, f"스냅 오차가 {worst:.0f}m 입니다 — 간격의 절반을 넘습니다"

    def test_junctions_are_never_removed(self, tmp_path):
        """교차로는 경로 선택지 그 자체다. 지우면 갈 수 있는 길이 사라진다."""
        graph = self._load(tmp_path, _grid_geojson())
        degrees = [len(edges) for edges in graph.nodes.values()]
        assert any(d > 2 for d in degrees), "격자인데 분기 노드가 없습니다"

    def test_a_ring_road_survives(self, tmp_path):
        """교차로가 하나도 없는 고리는 시작점이 없다. 처리하지 않으면 통째로 사라진다."""
        ring = [
            [BASE_LON, BASE_LAT],
            [BASE_LON + 0.01, BASE_LAT],
            [BASE_LON + 0.01, BASE_LAT + 0.01],
            [BASE_LON, BASE_LAT + 0.01],
            [BASE_LON, BASE_LAT],
        ]
        payload = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "way/ring",
                    "properties": {"highway": "residential"},
                    "geometry": {"type": "LineString", "coordinates": ring},
                }
            ],
        }
        graph = self._load(tmp_path, payload)
        assert len(graph) > 0, "고리가 통째로 사라졌습니다"
        assert graph.nearest_node(BASE_LAT, BASE_LON, max_distance_m=200.0) is not None


class TestGraphCache:
    """그래프 구축은 경북 전역이면 수단당 2분이 넘는다. 원본이 그대로면 다시
    만들 이유가 없으므로 직렬화해 두고 꺼내 쓴다."""

    def _load(self, tmp_path, payload=None):
        path = tmp_path / "roads.geojson"
        path.write_text(json.dumps(payload or _grid_geojson()), encoding="utf-8")
        return path, load_road_graph(path, profile_for(TransportMode.FOOT))

    def test_a_cache_is_written_and_reused(self, tmp_path):
        path, first = self._load(tmp_path)
        assert len(list((tmp_path / ".graph-cache").glob("*.pickle"))) == 1

        second = load_road_graph(path, profile_for(TransportMode.FOOT))
        assert len(second) == len(first)
        assert second.edge_count == first.edge_count

    def test_the_cached_graph_is_the_contracted_one(self, tmp_path):
        """캐시를 축약 전에 저장하면, 캐시가 있을 때만 그래프가 축약되지 않은
        채로 돌아온다 — 캐시가 있을수록 느려지고 무거워지는 상태가 된다.
        실제로 그렇게 배포된 적이 있어서 여기서 잡는다."""
        chain = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "way/1",
                    "properties": {"highway": "residential"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[BASE_LON + i * 0.0001, BASE_LAT] for i in range(200)],
                    },
                }
            ],
        }
        path, built = self._load(tmp_path, chain)
        from_cache = load_road_graph(path, profile_for(TransportMode.FOOT))

        assert len(from_cache) == len(built)
        assert len(from_cache) < 30, "캐시에서 온 그래프가 축약되지 않았습니다"

    def test_a_changed_source_is_not_served_from_the_old_cache(self, tmp_path):
        path, first = self._load(tmp_path)

        smaller = _grid_geojson()
        smaller["features"] = smaller["features"][:2]
        path.write_text(json.dumps(smaller), encoding="utf-8")

        second = load_road_graph(path, profile_for(TransportMode.FOOT))
        assert second.way_count != first.way_count

    def test_a_corrupt_cache_falls_back_to_building(self, tmp_path):
        path, built = self._load(tmp_path)
        next((tmp_path / ".graph-cache").glob("*.pickle")).write_bytes(b"not a pickle")

        recovered = load_road_graph(path, profile_for(TransportMode.FOOT))
        assert len(recovered) == len(built)

    def test_an_unwritable_cache_directory_does_not_break_loading(self, tmp_path, monkeypatch):
        """읽기 전용 볼륨에서도 경로 계산은 되어야 한다. 캐시는 최적화지 조건이 아니다."""
        from app.routing import graph as graph_module

        path = tmp_path / "roads.geojson"
        path.write_text(json.dumps(_grid_geojson()), encoding="utf-8")

        def refuse(*args, **kwargs):
            raise OSError("read-only file system")

        monkeypatch.setattr(graph_module.Path, "mkdir", refuse)
        assert len(graph_module.load_road_graph(path, profile_for(TransportMode.FOOT))) > 0

    def test_nearest_node_survives_a_round_trip(self, tmp_path):
        """공간 인덱스는 repr 에서 빠지는 비공개 필드다. 직렬화에서 누락되면
        캐시로 읽은 그래프는 출발지를 도로에 붙이지 못한다."""
        path, built = self._load(tmp_path)
        from_cache = load_road_graph(path, profile_for(TransportMode.FOOT))

        assert from_cache.nearest_node(BASE_LAT, BASE_LON) == built.nearest_node(BASE_LAT, BASE_LON)
        assert from_cache.nearest_node(BASE_LAT, BASE_LON) is not None
