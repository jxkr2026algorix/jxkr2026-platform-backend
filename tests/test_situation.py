"""상황판 — 봉투 전달과 가용성 노출."""

from __future__ import annotations

from tests.fakes import ALL_FAILED_ENVELOPE


async def test_context_carries_state_and_capability(client, seeded):
    response = await client.get(
        "/api/v1/situation/context", params={"region": "청송군", "hazard": "landslide"}
    )
    assert response.status_code == 200
    body = response.json()

    assert body["state"] == "DATA"
    assert body["capability"]["readiness"] == "ready"
    assert body["hazard_korean"] == "산사태"
    assert body["envelope"]["complete"] is False
    assert body["envelope"]["failed_sources"] == ["landslide_forecast"]
    assert "위험 없음" in body["headline_caveat"]


async def test_context_keeps_full_envelope(client, seeded):
    body = (
        await client.get(
            "/api/v1/situation/context", params={"region": "청송군", "hazard": "landslide"}
        )
    ).json()
    envelope = body["envelope"]
    for key in ("records", "citations", "receipts", "degradations", "absence_confirmed"):
        assert key in envelope, f"{key} 가 봉투에서 사라졌다"


async def test_all_failed_context_is_unverified(client, seeded, fake_gbsafe):
    fake_gbsafe.envelope = ALL_FAILED_ENVELOPE
    body = (
        await client.get(
            "/api/v1/situation/context", params={"region": "청송군", "hazard": "landslide"}
        )
    ).json()
    assert body["state"] == "UNVERIFIED"
    assert body["envelope"]["records"] == []


async def test_partial_hazard_exposes_its_limit(client, seeded):
    """지진은 partial 이다 — 갈 곳을 말할 수 없다는 사실이 응답에 실려야 한다."""
    body = (
        await client.get("/api/v1/meta/hazards")
    ).json()
    earthquake = next(h for h in body["hazards"] if h["hazard"] == "earthquake")
    assert earthquake["readiness"] == "partial"
    assert earthquake["can_detect"] is True
    assert earthquake["can_say_where_to_go"] is False
    assert earthquake["caveat"]
    assert "earthquake" in body["partial"]
    assert "earthquake" not in body["ready"]


async def test_map_scenario_is_attached(client, seeded):
    body = (await client.get("/api/v1/meta/hazards")).json()
    landslide = next(h for h in body["hazards"] if h["hazard"] == "landslide")
    assert landslide["map_scenario"] == "landslide"
