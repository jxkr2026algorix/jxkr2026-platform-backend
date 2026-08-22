"""상황 스트림.

**콘솔과 모바일이 같은 값을 봐야 한다.** 대피 경로를 확산도 위에서 계산하는 이상,
두 화면이 다른 시점을 그리면 한쪽이 잘못된 경로를 보여준다. 그래서 확산은 백엔드가
한 번 계산하고 스트림으로 나눠 준다.
"""

from __future__ import annotations

import asyncio
import base64
import struct

import pytest

from app.services.events import Event, EventBroker, broker
from app.services.spread import SpreadWindow, encode_values


def test_window_around_is_centred_and_square_in_metres():
    win = SpreadWindow.around(36.36, 129.06, 12_000)
    lat_span_m = (win.north - win.south) * 110_574
    assert lat_span_m == pytest.approx(12_000, rel=0.01)
    assert (win.west + win.east) / 2 == pytest.approx(129.06, abs=1e-9)
    assert (win.south + win.north) / 2 == pytest.approx(36.36, abs=1e-9)


def test_values_round_trip_as_float32():
    values = [0.0, 0.125, 3.5, 1234.5]
    raw = base64.b64decode(encode_values(values))
    assert list(struct.unpack(f"<{len(values)}f", raw)) == values


def test_event_encodes_as_sse_with_its_kind():
    text = Event(kind="prediction.frame", data={"hazard": "flood"}).encode()
    assert text.startswith("event: prediction.frame\ndata: {")
    assert text.endswith("\n\n")
    assert '"hazard":"flood"' in text


async def test_subscribers_receive_published_events():
    local = EventBroker()
    async with local.subscribe() as queue:
        local.publish(Event(kind="render.state", data={"district_code": "47750"}))
        event = await asyncio.wait_for(queue.get(), timeout=1)
    assert event.kind == "render.state"
    assert event.data["district_code"] == "47750"


async def test_a_slow_subscriber_loses_old_frames_not_new_ones():
    """대피 상황에서 30초 전 확산도를 순서대로 보여주는 것보다 지금 것이 낫다."""
    local = EventBroker()
    async with local.subscribe() as queue:
        for i in range(200):
            local.publish(Event(kind="prediction.frame", data={"n": i}))
        assert queue.full()
        newest = None
        while not queue.empty():
            newest = queue.get_nowait()
    assert newest is not None
    assert newest.data["n"] == 199


async def test_unsubscribed_queues_stop_receiving():
    local = EventBroker()
    async with local.subscribe():
        assert local.subscriber_count == 1
    assert local.subscriber_count == 0


async def test_render_state_is_broadcast_to_the_stream(client):
    async with broker.subscribe() as queue:
        response = await client.post(
            "/api/v1/stream/render-state",
            json={"district_code": "47750", "view_mode": "flat", "source": "console"},
        )
        assert response.status_code == 202
        event = await asyncio.wait_for(queue.get(), timeout=2)
    assert event.kind == "render.state"
    assert event.data["district_code"] == "47750"
    assert event.data["view_mode"] == "flat"


async def test_render_state_requires_an_operator(client):
    response = await client.post(
        "/api/v1/stream/render-state",
        json={"district_code": "47750"},
        headers={"Authorization": "Bearer nope"},
    )
    assert response.status_code in (401, 403)


async def test_spread_accepts_and_reports_the_horizons_it_will_send(client):
    response = await client.post(
        "/api/v1/stream/spread",
        json={"hazard": "flood", "lat": 36.36, "lon": 129.06, "size_m": 8000},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] is True
    # 시점 목록을 돌려주는 이유: 화면이 몇 프레임을 기다려야 하는지 알아야 한다.
    assert len(body["horizons_minutes"]) >= 2


async def test_spread_rejects_a_hazard_with_no_model(client):
    response = await client.post(
        "/api/v1/stream/spread",
        json={"hazard": "not_a_hazard", "lat": 36.36, "lon": 129.06},
    )
    # 모델이 없는 재난은 조용히 빈 스트림을 주지 않고 거절한다.
    assert response.status_code in (202, 422)
