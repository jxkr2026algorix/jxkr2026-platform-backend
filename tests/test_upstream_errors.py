"""상류 오류가 빈 데이터로 둔갑하지 않는가."""

from __future__ import annotations

import httpx
import pytest

from app.clients.gbsafe import GbSafeClient
from app.core.config import Settings
from app.core.errors import UpstreamError, UpstreamTimeout


def _client(handler) -> GbSafeClient:
    settings = Settings(gbsafe_base_url="http://upstream.test", api_keys="")
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://upstream.test")
    return GbSafeClient(settings, client=http_client)


async def test_403_raises_instead_of_returning_empty():
    """403 을 빈 목록으로 삼키면 '위험 없음'이 된다. 반드시 예외여야 한다."""

    def handler(request):
        return httpx.Response(403, json={"error": "denied"})

    client = _client(handler)
    with pytest.raises(UpstreamError) as excinfo:
        await client.hazard_context("문경시", "landslide")
    assert excinfo.value.upstream_status == 403
    await client.aclose()


async def test_timeout_raises_upstream_timeout():
    def handler(request):
        raise httpx.ReadTimeout("too slow", request=request)

    client = _client(handler)
    with pytest.raises(UpstreamTimeout):
        await client.hazard_context("문경시")
    await client.aclose()


async def test_non_json_response_raises():
    def handler(request):
        return httpx.Response(200, text="<html>gateway</html>")

    client = _client(handler)
    with pytest.raises(UpstreamError):
        await client.hazard_context("문경시")
    await client.aclose()


async def test_partial_upstream_failure_is_not_an_error():
    """일부 원천 실패는 상류가 200 + complete=false 로 준다. 여기서 예외를 던지면
    나머지 원천의 값이 통째로 사라진다."""
    from tests.fakes import PARTIAL_FAILURE_ENVELOPE

    def handler(request):
        return httpx.Response(200, json=PARTIAL_FAILURE_ENVELOPE)

    client = _client(handler)
    envelope = await client.hazard_context("문경시", "landslide")
    assert envelope.records
    assert envelope.complete is False
    assert envelope.state == "DATA"
    await client.aclose()


async def test_cache_does_not_store_failures():
    """실패를 캐시하면 일시 장애가 TTL 동안 굳는다."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"count": 0, "regions": []})

    client = _client(handler)
    with pytest.raises(UpstreamError):
        await client.regions()
    result = await client.regions()
    assert result == {"count": 0, "regions": []}
    assert calls["n"] == 2
    await client.aclose()


async def test_upstream_error_becomes_502_problem_json(client, seeded, fake_gbsafe):
    async def boom(*args, **kwargs):
        raise UpstreamError("상류 죽음", upstream="gbsafedata", upstream_status=503)

    fake_gbsafe.hazard_context = boom
    response = await client.get(
        "/api/v1/situation/context", params={"region": "청송군", "hazard": "landslide"}
    )
    assert response.status_code == 502
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["upstream"] == "gbsafedata"
    assert body["upstream_status"] == 503
