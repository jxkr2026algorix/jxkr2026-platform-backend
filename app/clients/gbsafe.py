"""GB SafeData 클라이언트 — 읽기 전용 상류.

https://datainfra.salgil.gyeongbuk.kr

지켜야 할 계약 세 가지.

1. **실패를 빈 결과로 바꾸지 않는다.** 상류가 403/타임아웃이면 `UpstreamError` 를 던진다.
   `except: return []` 를 쓰면 조회 실패가 화면에서 '위험 없음'이 된다. 이 저장소에서
   실제로 잡힌 가장 위험한 결함이 그것이었다.
2. **봉투를 줄이지 않는다.** records 만 꺼내면 complete/absence_confirmed/freshness 가 사라진다.
3. **브라우저에서 직접 부르지 않는다.** 상류는 정부 인증키로 호출하므로 이 백엔드가 대신 부른다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.cache import TTLCache
from app.core.config import Settings
from app.core.errors import UpstreamError, UpstreamTimeout
from app.core.logging import get_logger
from app.schemas.common import Envelope

log = get_logger(__name__)

UPSTREAM = "gbsafedata"

# 자주 바뀌지 않는 것들은 더 길게 잡는다. 지역 목록이 1분마다 바뀔 리 없다.
_LONG_TTL_S = 900


class GbSafeClient:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._base_url = settings.gbsafe_base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(settings.gbsafe_timeout_s, connect=10.0),
            headers={
                "accept": "application/json",
                "user-agent": "salgil-platform-backend/0.1 (+jxkr2026)",
            },
            follow_redirects=True,
        )
        self._cache = TTLCache(default_ttl_s=settings.gbsafe_cache_ttl_s)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ── 저수준 ────────────────────────────────────────────────────────────────

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        try:
            response = await self._client.get(path, params=params, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise UpstreamTimeout(
                f"{path} 응답이 시간 안에 오지 않았습니다 ({exc.__class__.__name__})",
                upstream=UPSTREAM,
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"{path} 요청에 실패했습니다: {exc}", upstream=UPSTREAM) from exc

        if response.status_code >= 400:
            raise UpstreamError(
                f"{path} 이(가) HTTP {response.status_code} 로 응답했습니다: {response.text[:300]}",
                upstream=UPSTREAM,
                upstream_status=response.status_code,
            )

        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError(
                f"{path} 응답이 JSON 이 아닙니다: {response.text[:200]}",
                upstream=UPSTREAM,
                upstream_status=response.status_code,
            ) from exc

    async def _cached_get(
        self, key: str, path: str, *, params: dict[str, Any] | None = None, ttl_s: int | None = None
    ) -> Any:
        return await self._cache.get_or_set(key, lambda: self._get(path, params=params), ttl_s)

    # ── 메타 ──────────────────────────────────────────────────────────────────

    async def service_info(self) -> dict[str, Any]:
        return await self._cached_get("root", "/", ttl_s=_LONG_TTL_S)

    async def regions(self) -> dict[str, Any]:
        return await self._cached_get("regions", "/v1/regions", ttl_s=_LONG_TTL_S)

    async def resolve_region(self, query: str) -> dict[str, Any]:
        return await self._cached_get(
            f"resolve:{query}", "/v1/regions/resolve", params={"q": query}, ttl_s=_LONG_TTL_S
        )

    async def hazard_types(self) -> dict[str, Any]:
        return await self._cached_get("hazard-types", "/v1/hazard-types", ttl_s=_LONG_TTL_S)

    async def capabilities(self) -> dict[str, Any]:
        # 가용성은 원천 심의 승인 등으로 바뀐다. 그래도 분 단위로 바뀌지는 않는다.
        return await self._cached_get("capabilities", "/v1/hazards/capabilities", ttl_s=300)

    async def health(self) -> dict[str, Any]:
        return await self._cached_get("health", "/v1/health", ttl_s=30)

    async def agent_system_prompt(self) -> dict[str, Any]:
        """챗봇용 시스템 프롬프트.

        복붙하지 않고 매번 받아 쓴다. 상류가 고치면 자동으로 따라오게 하기 위해서다.
        도구만 붙이고 이 프롬프트를 빼면, 모델이 403 실패를 '위험 없음'으로 답한다.
        """
        return await self._cached_get("system-prompt", "/v1/agent/system-prompt", ttl_s=_LONG_TTL_S)

    async def tools(self) -> dict[str, Any]:
        return await self._cached_get("tools", "/v1/tools", ttl_s=_LONG_TTL_S)

    async def call_tool(self, name: str, params: dict[str, Any]) -> Any:
        # 도구 실행은 캐시하지 않는다 — 관측값이다.
        return await self._get(f"/v1/tools/{name}", params=params, timeout=self._context_timeout)

    # ── 데이터 ────────────────────────────────────────────────────────────────

    @property
    def _context_timeout(self) -> float:
        return self._settings.gbsafe_context_timeout_s

    async def hazard_context(self, region: str, hazard: str | None = None) -> Envelope:
        """특정 지역의 현재 위험 상황. 여러 원천을 병렬 조회하므로 느리다.

        일부 원천이 실패해도 상류는 200 으로 답하고 `complete=false` 를 준다.
        그 경우 여기서 예외를 던지면 안 된다 — 나머지 원천의 값이 사라진다.
        """
        params: dict[str, Any] = {"region": region}
        if hazard:
            params["hazard"] = hazard
        key = f"context:{region}:{hazard or '-'}"
        payload = await self._cache.get_or_set(
            key,
            lambda: self._get("/v1/hazards/context", params=params, timeout=self._context_timeout),
        )
        return Envelope.from_upstream(payload)

    async def source(
        self, connector: str, region: str | None = None, rows: int | None = None
    ) -> Envelope:
        params: dict[str, Any] = {}
        if region:
            params["region"] = region
        if rows is not None:
            params["rows"] = rows
        payload = await self._get(
            f"/v1/sources/{connector}", params=params, timeout=self._context_timeout
        )
        return Envelope.from_upstream(payload)

    async def datasets(self, **params: Any) -> dict[str, Any]:
        clean = {k: v for k, v in params.items() if v is not None}
        return await self._get("/v1/datasets", params=clean)

    async def dataset(self, dataset_id: str) -> dict[str, Any]:
        return await self._cached_get(
            f"dataset:{dataset_id}", f"/v1/datasets/{dataset_id}", ttl_s=_LONG_TTL_S
        )

    async def dataset_citation(self, dataset_id: str) -> dict[str, Any]:
        return await self._cached_get(
            f"cite:{dataset_id}", f"/v1/datasets/{dataset_id}/citation", ttl_s=_LONG_TTL_S
        )

    async def verify_dataset(self, dataset_id: str, operation: str = "read") -> dict[str, Any]:
        """이 용도로 써도 되는지 판정.

        라이선스가 허용해도 개발단계 심의 대기면 allowed=false 다. 재투영·클리핑·조인은
        `derive` 다 — KOGL 3·4(변경금지)에서 막힌다.
        """
        return await self._cached_get(
            f"verify:{dataset_id}:{operation}",
            f"/v1/datasets/{dataset_id}/verify",
            params={"operation": operation},
            ttl_s=_LONG_TTL_S,
        )

    async def quality(self) -> dict[str, Any]:
        return await self._cached_get("quality", "/v1/quality", ttl_s=_LONG_TTL_S)

    async def ping(self) -> tuple[bool, str | None, float | None]:
        """준비 상태 점검용. 여기서만 예외를 삼킨다 — 판정 결과 자체가 반환값이라서다."""
        started = datetime.now(UTC)
        try:
            await self._get("/", timeout=5.0)
        except UpstreamError as exc:
            return False, exc.detail, None
        elapsed_ms = (datetime.now(UTC) - started).total_seconds() * 1000
        return True, None, round(elapsed_ms, 1)
