"""예측 — 자체 모델 표시가 지워지지 않는가."""

from __future__ import annotations


async def test_stub_prediction_is_labelled(client, seeded):
    response = await client.post(
        "/api/v1/predictions",
        json={
            "recipe": "landslide_risk",
            "region_code": "47750",
            "hazard": "landslide",
            "grid": {"height": 8, "width": 8},
            "threshold": 0.5,
        },
    )
    assert response.status_code == 201
    body = response.json()

    assert body["is_derived"] is True
    assert body["derived_notice"]
    assert body["is_stub"] is True
    assert body["feature_mode"] == "synthetic"
    assert body["warnings"]
    assert body["model"]["backend"] == "stub"


async def test_stub_is_deterministic(client, seeded):
    payload = {
        "recipe": "landslide_risk",
        "region_code": "47750",
        "grid": {"height": 8, "width": 8},
    }
    first = (await client.post("/api/v1/predictions", json=payload)).json()
    second = (await client.post("/api/v1/predictions", json=payload)).json()
    assert first["outputs"][0]["data"] == second["outputs"][0]["data"]
    assert first["prediction_id"] != second["prediction_id"]


async def test_summary_shape(client, seeded):
    body = (
        await client.post(
            "/api/v1/predictions",
            json={
                "recipe": "wildfire_spread",
                "region_code": "47750",
                "grid": {"height": 10, "width": 10},
                "threshold": 0.4,
            },
        )
    ).json()
    summary = body["summary"]
    assert summary["total_cells"] == 100
    assert summary["threshold"] == 0.4
    assert 0.0 <= summary["mean"] <= 1.0
    assert summary["max"] >= summary["mean"]
    assert len(summary["top_cells"]) == 5


async def test_run_is_recorded(client, seeded):
    await client.post(
        "/api/v1/predictions",
        json={"recipe": "rain_nowcast", "region_code": "47750", "grid": {"height": 4, "width": 4}},
    )
    runs = (await client.get("/api/v1/predictions/runs", params={"recipe": "rain_nowcast"})).json()
    assert runs
    assert runs[0]["recipe"] == "rain_nowcast"
    assert runs[0]["is_stub"] is True
    assert runs[0]["summary"]["total_cells"] == 16


async def test_unknown_recipe_rejected(client, seeded):
    response = await client.post(
        "/api/v1/predictions", json={"recipe": "volcano_eruption", "region_code": "47750"}
    )
    assert response.status_code == 422


async def test_catalog_lists_every_recipe(client, seeded):
    body = (await client.get("/api/v1/predictions/models")).json()
    names = {m["recipe"] for m in body["models"]}
    assert "landslide_risk" in names
    assert "wildfire_spread" in names
    assert body["served_by"] == "stub"
