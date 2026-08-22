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


async def test_upstream_client_error_becomes_422_not_502(client, seeded, monkeypatch):
    """ML 서버가 4xx 를 주면 우리 요청이 틀린 것이다 — 502 로 올리면 엉뚱한 곳을 보게 된다."""
    import httpx

    from app.clients.mlengine import MlEngineClient
    from app.core.config import Settings

    def handler(request):
        return httpx.Response(
            422,
            json={"title": "입력이 모델과 맞지 않습니다", "detail": "16×16 격자를 받습니다"},
        )

    settings = Settings(mlengine_mode="http", mlengine_base_url="http://ml.test", api_keys="")
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ml.test"
    )
    client._transport.app.state.mlengine = MlEngineClient(settings, client=http_client)

    response = await client.post(
        "/api/v1/predictions",
        json={
            "recipe": "landslide_risk",
            "region_code": "47750",
            "grid": {"height": 64, "width": 64},
        },
    )
    assert response.status_code == 422
    assert "16×16" in response.json()["detail"]


async def test_upstream_auth_failure_is_502_with_hint(client, seeded):
    import httpx

    from app.clients.mlengine import MlEngineClient
    from app.core.config import Settings

    def handler(request):
        return httpx.Response(403, json={"detail": "알 수 없는 토큰입니다"})

    settings = Settings(mlengine_mode="http", mlengine_base_url="http://ml.test", api_keys="")
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://ml.test"
    )
    client._transport.app.state.mlengine = MlEngineClient(settings, client=http_client)

    response = await client.post(
        "/api/v1/predictions", json={"recipe": "landslide_risk", "region_code": "47750"}
    )
    assert response.status_code == 502
    assert "SALGIL_MLENGINE_API_KEY" in response.json()["detail"]


async def test_readiness_detects_bad_ml_token(client, seeded):
    """토큰을 compose 에 넣는 것을 잊는 것이 가장 흔한 배포 실수다.

    인증 없는 /readyz 를 찌르면 '준비됨'으로 보고되고, 첫 추론에서야 403 을 만난다.
    그래서 인증이 걸린 /v1/ping 을 부른다.
    """
    import httpx

    from app.clients.mlengine import MlEngineClient
    from app.core.config import Settings

    seen: list[str] = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(403, json={"detail": "알 수 없는 토큰입니다"})

    settings = Settings(mlengine_mode="http", mlengine_base_url="http://ml.test", api_keys="")
    ml = MlEngineClient(
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ml.test"),
    )

    ok, detail, _ = await ml.ping()
    assert ok is False
    assert "SALGIL_MLENGINE_API_KEY" in detail
    assert seen == ["/v1/ping"]  # 인증 없는 /readyz 를 부르지 않는다
    await ml.aclose()


async def test_readiness_flags_triton_not_loaded(client, seeded):
    """게이트웨이는 살아 있는데 Triton 이 안 떠 있으면 준비된 것이 아니다."""
    import httpx

    from app.clients.mlengine import MlEngineClient
    from app.core.config import Settings

    def handler(request):
        return httpx.Response(200, json={"ok": True, "backend": "triton", "triton_ready": False})

    settings = Settings(mlengine_mode="http", mlengine_base_url="http://ml.test", api_keys="")
    ml = MlEngineClient(
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ml.test"),
    )
    ok, detail, _ = await ml.ping()
    assert ok is False
    assert "Triton" in detail
    await ml.aclose()


async def test_failed_run_survives_the_rollback(client, seeded, session):
    """실패 기록은 별도 트랜잭션이어야 한다.

    요청 세션에 넣으면 예외와 함께 롤백돼, 정확히 남겨야 할 순간에 아무것도 남지 않는다.
    """
    import httpx
    from sqlalchemy import select

    from app.clients.mlengine import MlEngineClient
    from app.core.config import Settings
    from app.db.models import PredictionRun

    def handler(request):
        return httpx.Response(503, json={"detail": "모델이 Triton 에 로드되지 않았습니다"})

    settings = Settings(mlengine_mode="http", mlengine_base_url="http://ml.test", api_keys="")
    client._transport.app.state.mlengine = MlEngineClient(
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ml.test"),
    )

    response = await client.post(
        "/api/v1/predictions",
        json={"recipe": "landslide_risk", "region_code": "47750"},
    )
    assert response.status_code == 502

    rows = (
        (await session.execute(select(PredictionRun).where(PredictionRun.status == "failed")))
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].recipe == "landslide_risk"
    assert "Triton" in rows[0].error_detail


async def test_client_error_is_also_recorded(client, seeded, session):
    """422(우리 요청이 틀림)도 이력이다 — 무엇을 잘못 보냈는지 나중에 봐야 한다."""
    import httpx
    from sqlalchemy import select

    from app.clients.mlengine import MlEngineClient
    from app.core.config import Settings
    from app.db.models import PredictionRun

    def handler(request):
        return httpx.Response(422, json={"detail": "16×16 격자를 받습니다"})

    settings = Settings(mlengine_mode="http", mlengine_base_url="http://ml.test", api_keys="")
    client._transport.app.state.mlengine = MlEngineClient(
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ml.test"),
    )

    response = await client.post(
        "/api/v1/predictions",
        json={"recipe": "landslide_risk", "region_code": "47750"},
    )
    assert response.status_code == 422

    rows = (
        (await session.execute(select(PredictionRun).where(PredictionRun.status == "failed")))
        .scalars()
        .all()
    )
    assert len(rows) == 1


async def test_channel_summary_survives_to_the_client(client, seeded):
    """재난별 요약이 봉투에서 사라지면 화면은 가뭄 경고와 폭염 경고를 구분할 수 없다."""
    import httpx

    from app.clients.mlengine import MlEngineClient
    from app.core.config import Settings

    payload = {
        "prediction_id": "b0f2c0f0-0000-4000-8000-000000000001",
        "recipe": "weather_extremes",
        "status": "succeeded",
        "model": {"name": "weather_extremes", "version": "1", "backend": "triton"},
        "region_code": "47750",
        "summary": {
            "max": 0.53,
            "mean": 0.48,
            "threshold": 0.5,
            "cells_over_threshold": 64,
            "total_cells": 256,
            "channels": [
                {"channel": 0, "hazard": "heatwave", "max": 0.50, "cells_over_threshold": 1},
                {"channel": 3, "hazard": "drought", "max": 0.53, "cells_over_threshold": 63},
            ],
        },
        "feature_mode": "synthetic",
        "is_stub": False,
        "is_derived": True,
        "derived_notice": "자체 모델",
    }

    def handler(request):
        return httpx.Response(200, json=payload)

    settings = Settings(mlengine_mode="http", mlengine_base_url="http://ml.test", api_keys="")
    client._transport.app.state.mlengine = MlEngineClient(
        settings,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ml.test"),
    )

    response = await client.post(
        "/api/v1/predictions",
        json={"recipe": "weather_extremes", "region_code": "47750"},
    )
    assert response.status_code == 201
    channels = response.json()["summary"]["channels"]
    assert [c["hazard"] for c in channels] == ["heatwave", "drought"]
    assert channels[1]["cells_over_threshold"] == 63


async def test_channel_summary_is_kept_in_the_run_history(client, seeded, session):
    """나중에 '그때 어느 재난이 위험했는가'에 답하려면 이력에도 남아야 한다."""
    from sqlalchemy import select

    from app.db.models import PredictionRun

    await client.post(
        "/api/v1/predictions",
        json={"recipe": "weather_extremes", "region_code": "47750"},
    )
    rows = (
        (await session.execute(select(PredictionRun).where(PredictionRun.status == "succeeded")))
        .scalars()
        .all()
    )
    assert rows
    assert "channels" in rows[-1].summary
