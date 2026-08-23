"""경로 API — 대피소 선택·현장 보고 반영·출처 표시."""

from __future__ import annotations

import json

import pytest

from tests.test_routing_core import BASE_LAT, BASE_LON, STEP, _grid_geojson

APPROVER = {"Authorization": "Bearer test-ap"}
FIELD = {"Authorization": "Bearer test-fd"}


@pytest.fixture
def road_network(tmp_path, monkeypatch):
    from app.core.config import get_settings
    from app.services import routing

    path = tmp_path / "roads.geojson"
    path.write_text(json.dumps(_grid_geojson()), encoding="utf-8")
    monkeypatch.setenv("SALGIL_ROAD_NETWORK_PATH", str(path))
    get_settings.cache_clear()
    routing._load_graph_cached.cache_clear()
    yield path
    get_settings.cache_clear()
    routing._load_graph_cached.cache_clear()


@pytest.fixture
async def geo_fixtures(session):
    """도로망 격자 위에 마을 하나와 대피소 둘."""
    from app.db.models import Community, Shelter

    community = Community(
        region_code="47750",
        region_name="청송군",
        name="격자마을",
        residents=10,
        households=5,
        lat=BASE_LAT,
        lon=BASE_LON,
        data_mode="synthetic",
    )
    near = Shelter(
        region_code="47750",
        name="가까운 대피소",
        lat=BASE_LAT + 2 * STEP,
        lon=BASE_LON + 2 * STEP,
        hazards=["wildfire", "landslide"],
        capacity=100,
        capacity_basis="demo",
        data_mode="synthetic",
    )
    quake_only = Shelter(
        region_code="47750",
        name="지진 전용 대피소",
        lat=BASE_LAT + 2 * STEP,
        lon=BASE_LON,
        hazards=["earthquake"],
        capacity=50,
        data_mode="synthetic",
    )
    session.add_all([community, near, quake_only])
    await session.commit()
    return {"community": community, "near": near, "quake": quake_only}


async def test_modes_are_listed(client, seeded):
    body = (await client.get("/api/v1/routing/modes")).json()
    modes = {m["mode"]: m for m in body["modes"]}
    assert set(modes) == {"foot", "assisted", "bicycle", "car"}
    assert modes["assisted"]["speed_kmh"] < modes["foot"]["speed_kmh"]
    assert "OpenStreetMap" in body["attribution"]


async def test_routing_without_a_road_network_is_refused(client, seeded, geo_fixtures):
    """도로망 없이 경로를 내면 지도 위에 그럴듯한 직선이 그려질 뿐이다."""
    response = await client.post(
        "/api/v1/routing/evacuation",
        json={
            "community_id": str(geo_fixtures["community"].id),
            "hazard": "wildfire",
            "use_prediction": False,
        },
    )
    assert response.status_code == 422
    assert "SALGIL_ROAD_NETWORK_PATH" in response.json()["detail"]


async def test_route_to_shelter(client, seeded, geo_fixtures, road_network):
    response = await client.post(
        "/api/v1/routing/evacuation",
        json={
            "community_id": str(geo_fixtures["community"].id),
            "hazard": "wildfire",
            "mode": "foot",
            "use_prediction": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["recommended"] == str(geo_fixtures["near"].id)
    leg = body["routes"][0]
    assert leg["found"] is True
    assert len(leg["geometry"]) >= 3
    assert leg["distance_m"] > 0
    assert leg["duration_minutes"] > 0
    # 출처와 한계 표시는 지워지면 안 된다
    assert "OpenStreetMap" in body["attribution"]
    assert body["is_derived"] is True
    assert "공식 안전경로가 아닙니다" in body["notice"]


async def test_shelters_are_filtered_by_hazard(client, seeded, geo_fixtures, road_network):
    """지진 전용 대피소가 산불 경로 후보에 들어오면 안 된다."""
    body = (
        await client.post(
            "/api/v1/routing/evacuation",
            json={
                "community_id": str(geo_fixtures["community"].id),
                "hazard": "wildfire",
                "use_prediction": False,
            },
        )
    ).json()
    names = {leg["shelter_name"] for leg in body["routes"]}
    assert "가까운 대피소" in names
    assert "지진 전용 대피소" not in names


async def test_explicit_shelter_of_the_wrong_hazard_is_refused(
    client, seeded, geo_fixtures, road_network
):
    response = await client.post(
        "/api/v1/routing/evacuation",
        json={
            "community_id": str(geo_fixtures["community"].id),
            "hazard": "wildfire",
            "shelter_id": str(geo_fixtures["quake"].id),
            "use_prediction": False,
        },
    )
    assert response.status_code == 422
    assert "담당하지 않습니다" in response.json()["detail"]


async def test_prediction_skipped_is_stated_in_warnings(client, seeded, geo_fixtures, road_network):
    body = (
        await client.post(
            "/api/v1/routing/evacuation",
            json={
                "community_id": str(geo_fixtures["community"].id),
                "hazard": "wildfire",
                "use_prediction": False,
            },
        )
    ).json()
    assert body["prediction_used"] is False
    assert any("확산이 반영되지 않은" in w for w in body["warnings"])


async def test_field_report_closes_the_route(client, seeded, geo_fixtures, road_network):
    """현장이 통제를 보고하면 경로가 그 지점을 피하거나, 못 가면 사유를 준다."""
    incident = (
        await client.post(
            "/api/v1/incidents",
            json={
                "title": "산불",
                "region_code": "47750",
                "hazard": "wildfire",
                "level": 2,
            },
        )
    ).json()

    task = (
        await client.post(
            f"/api/v1/incidents/{incident['id']}/tasks",
            json={"title": "도로 확인", "kind": "verify_route", "priority": 1},
        )
    ).json()

    # 대피소 바로 앞을 막는다
    await client.post(
        f"/api/v1/tasks/{task['id']}/reports",
        json={
            "body": "진입로 유실",
            "observation": "route_blocked",
            "access_constraints": [
                {
                    "kind": "road_closed",
                    "location": "대피소 진입로",
                    "lat": BASE_LAT + 2 * STEP,
                    "lon": BASE_LON + 2 * STEP,
                }
            ],
        },
        headers=FIELD,
    )

    body = (
        await client.post(
            "/api/v1/routing/evacuation",
            json={
                "community_id": str(geo_fixtures["community"].id),
                "hazard": "wildfire",
                "incident_id": incident["id"],
                "use_prediction": False,
            },
        )
    ).json()

    assert body["field_reports_applied"] == 1
    leg = next(leg for leg in body["routes"] if leg["shelter_name"] == "가까운 대피소")
    assert leg["found"] is False
    assert leg["reason"]
    assert leg["geometry"] == []


async def test_unroutable_shelter_reports_a_reason_not_an_empty_route(
    client, seeded, geo_fixtures, road_network
):
    """'대피소가 없다'와 '길이 막혔다'를 화면이 구분할 수 있어야 한다."""
    from app.db.models import Shelter

    body = (
        await client.post(
            "/api/v1/routing/evacuation",
            json={
                "lat": BASE_LAT,
                "lon": BASE_LON,
                "hazard": "wildfire",
                "use_prediction": False,
            },
        )
    ).json()
    assert isinstance(Shelter, type)
    for leg in body["routes"]:
        assert leg["found"] or leg["reason"]


async def test_no_shelter_for_hazard_is_404(client, seeded, geo_fixtures, road_network):
    response = await client.post(
        "/api/v1/routing/evacuation",
        json={
            "community_id": str(geo_fixtures["community"].id),
            "hazard": "nuclear",
            "use_prediction": False,
        },
    )
    assert response.status_code == 404
    assert "대피소가 없습니다" in response.json()["detail"]


async def test_spreading_wildfire_changes_the_route(client, seeded, geo_fixtures, road_network):
    """같은 요청이라도 **언제 출발하느냐**에 따라 경로가 달라져야 한다.

    불은 30분 뒤에 가운데 칸에 닿는다. 지금 떠나면 3분 만에 지나가므로 그 길을 쓴다.
    28분 뒤에 떠나면 도착할 무렵 이미 타고 있으므로 돌아가야 한다.

    시간을 무시하면 두 경우가 같은 경로가 된다 — 출발 시점에는 둘 다 안전해 보이기 때문이다.
    """
    import httpx

    from app.clients.mlengine import MlEngineClient
    from app.core.config import Settings

    def handler(request):
        import json as _json

        horizon = _json.loads(request.content).get("horizon_minutes", 0)
        # 모델은 **로짓**을 낸다 (`logits` 텐서). 확률을 그대로 보내면 실제 계약과
        # 다른 것을 검증하게 된다 — 배포에서 정확히 그 착오로 모든 길이 막혔다.
        centre = 8.0 if horizon >= 30 else -8.0  # 시그모이드 후 ≈1.0 / ≈0.0
        safe = -8.0
        values = [safe, safe, safe, safe, centre, safe, safe, safe, safe]
        return httpx.Response(
            200,
            json={
                "prediction_id": "b0f2c0f0-0000-4000-8000-000000000009",
                "recipe": "wildfire_spread",
                "status": "succeeded",
                "model": {"name": "wildfire_spread", "version": "1", "backend": "triton"},
                # 모델이 격자의 좌표 범위를 실어 준다 — 백엔드가 위치를 추측하지 않는다.
                "grid": {
                    "height": 3,
                    "width": 3,
                    "crs": "EPSG:4326",
                    "bbox": [
                        BASE_LON - STEP / 2,
                        BASE_LAT - STEP / 2,
                        BASE_LON + 2 * STEP + STEP / 2,
                        BASE_LAT + 2 * STEP + STEP / 2,
                    ],
                },
                "outputs": [
                    {"name": "logits", "dtype": "float32", "shape": [3, 3], "data": values}
                ],
                "summary": {"total_cells": 9},
                "feature_mode": "real",
                "is_stub": False,
                "is_derived": True,
                "derived_notice": "자체 모델",
            },
        )

    settings = Settings(mlengine_mode="http", mlengine_base_url="http://ml.test", api_keys="")
    client._transport.app.state.mlengine = MlEngineClient(
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ml.test"),
    )

    async def route(depart_after_minutes: float) -> dict:
        response = await client.post(
            "/api/v1/routing/evacuation",
            json={
                "community_id": str(geo_fixtures["community"].id),
                "hazard": "wildfire",
                "use_prediction": True,
                "horizons_minutes": [0, 30, 60],
                "block_threshold": 0.5,
                "depart_after_minutes": depart_after_minutes,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    now = await route(0.0)
    assert now["prediction_used"] is True
    assert now["horizons_minutes"] == [0, 30, 60]
    assert now["prediction_model"].startswith("wildfire_spread@")
    # bbox 를 받았으므로 위치를 가정했다는 경고가 없어야 한다
    assert not any("가정" in w for w in now["warnings"])

    def passes_centre(geometry: list[list[float]]) -> bool:
        from app.routing.graph import haversine_m

        return any(
            haversine_m(lat, lon, BASE_LAT + STEP, BASE_LON + STEP) < 50 for lon, lat in geometry
        )

    immediate = next(x for x in now["routes"] if x["shelter_name"] == "가까운 대피소")
    assert immediate["found"] is True
    # 지금 떠나면 가운데 칸이 막히지 않는다 — 지나든 안 지나든 피할 이유가 없다
    assert immediate["avoided_edges"] == 0
    assert immediate["max_risk"] < 0.5

    later = await route(28.0)
    delayed = next(x for x in later["routes"] if x["shelter_name"] == "가까운 대피소")
    assert delayed["found"] is True
    # 28분 뒤 출발이면 도착할 무렵 타고 있으므로 그 칸을 못 쓴다
    assert delayed["avoided_edges"] > 0
    assert not passes_centre(delayed["geometry"])
    assert delayed["max_risk"] < 0.5


async def test_synthetic_prediction_does_not_close_roads(
    client, seeded, geo_fixtures, road_network
):
    """합성 입력으로 만든 위험장은 길을 막지 않는다.

    모델은 돌았지만 입력이 관측이 아니라, 그 출력은 불이 어디 있는지 말해 주지 않는다.
    그런 값으로 도로를 차단하면 '경로 없음'이라는 확신에 찬 오답이 나간다 —
    실제로는 '모른다'인데. 배포에서 모든 재난의 대피 경로가 그렇게 사라졌다.
    """
    body = (
        await client.post(
            "/api/v1/routing/evacuation",
            json={
                "community_id": str(geo_fixtures["community"].id),
                "hazard": "wildfire",
                "use_prediction": True,
                "horizons_minutes": [30],
            },
        )
    ).json()
    assert body["prediction_is_stub"] is True
    assert body["feature_mode"] == "synthetic"
    assert any("합성값" in w for w in body["warnings"])

    leg = next(x for x in body["routes"] if x["shelter_name"] == "가까운 대피소")
    assert leg["found"] is True
    assert leg["avoided_edges"] == 0


async def test_hazard_without_a_model_is_reported_not_silently_ignored(
    client, seeded, geo_fixtures, road_network
):
    body = (
        await client.post(
            "/api/v1/routing/evacuation",
            json={
                "community_id": str(geo_fixtures["community"].id),
                "hazard": "landslide",
                "use_prediction": True,
            },
        )
    ).json()
    assert body["prediction_used"] is True or any(
        "예측 모델이 없습니다" in w or "합성값" in w for w in body["warnings"]
    )
