"""경로 계산 코어 — 위험을 실제로 피하는가, 못 피하면 그렇다고 말하는가."""

from __future__ import annotations

import json

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
