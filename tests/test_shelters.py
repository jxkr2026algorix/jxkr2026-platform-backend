"""대피소 조회 — hazard 별 분리가 지켜지는가."""

from __future__ import annotations


async def test_hazard_is_required(client, seeded):
    response = await client.get("/api/v1/shelters", params={"region_code": "47750"})
    assert response.status_code == 422  # hazard 없이 물을 수 없다


async def test_shelters_are_not_auto_repurposed(client, seeded):
    """지진 대피소와 호우 대피소는 다른 시설이다."""
    landslide = await client.get(
        "/api/v1/shelters", params={"hazard": "landslide", "region_code": "47750"}
    )
    earthquake = await client.get(
        "/api/v1/shelters", params={"hazard": "earthquake", "region_code": "47750"}
    )
    assert landslide.status_code == 200
    assert len(landslide.json()) > 0
    assert earthquake.json() == []


async def test_distance_sorted_when_coordinates_given(client, seeded):
    response = await client.get(
        "/api/v1/shelters",
        params={"hazard": "landslide", "region_code": "47750", "lat": 36.3921, "lon": 129.1607},
    )
    rows = response.json()
    distances = [r["distance_km"] for r in rows]
    assert distances == sorted(distances)
    assert all(d is not None for d in distances)


async def test_capacity_basis_is_disclosed(client, seeded):
    """정원은 연 1회 갱신 파일 기준이다 — 실시간 수용현황이 아니라는 사실이 실려야 한다."""
    rows = (await client.get("/api/v1/shelters", params={"hazard": "landslide"})).json()
    assert all(r["capacity_basis"] for r in rows)


async def test_seed_data_is_marked_synthetic(client, seeded):
    """주민 수·세대수는 공개 데이터로 얻을 수 없다. 합성 표시가 지워지면 안 된다."""
    communities = (await client.get("/api/v1/communities")).json()["items"]
    assert communities
    assert all(c["data_mode"] == "synthetic" for c in communities)
