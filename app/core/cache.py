"""프로세스 내 TTL 캐시.

상류(GB SafeData)도 캐시하지만, 정부 API 호출 한도를 한 번 더 보호한다.
Redis 를 두지 않은 이유는 단순함이다 — 상태는 Postgres 가, 신선도는 상류가 소유한다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class _Entry:
    value: Any
    expires_at: float


class TTLCache:
    def __init__(self, *, default_ttl_s: int = 60, max_entries: int = 512) -> None:
        self._default_ttl_s = default_ttl_s
        self._max_entries = max_entries
        self._store: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        if entry.expires_at < time.monotonic():
            self._store.pop(key, None)
            return None
        return entry.value

    def set(self, key: str, value: Any, ttl_s: int | None = None) -> None:
        if len(self._store) >= self._max_entries:
            now = time.monotonic()
            expired = [k for k, e in self._store.items() if e.expires_at < now]
            for k in expired:
                self._store.pop(k, None)
            if len(self._store) >= self._max_entries:
                oldest = min(self._store, key=lambda k: self._store[k].expires_at)
                self._store.pop(oldest, None)
        ttl = self._default_ttl_s if ttl_s is None else ttl_s
        self._store[key] = _Entry(value=value, expires_at=time.monotonic() + ttl)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)

    def clear(self) -> None:
        self._store.clear()

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Awaitable[T]],
        ttl_s: int | None = None,
    ) -> T:
        """캐시 미스일 때 factory 를 한 번만 호출한다 (동시 요청은 하나로 합친다).

        factory 가 예외를 던지면 **캐시하지 않는다.** 실패를 캐시하면
        일시적 장애가 TTL 동안 굳어버린다.
        """
        hit = self.get(key)
        if hit is not None:
            return hit  # type: ignore[no-any-return]

        async with self._guard:
            lock = self._locks.setdefault(key, asyncio.Lock())

        async with lock:
            hit = self.get(key)
            if hit is not None:
                return hit  # type: ignore[no-any-return]
            value = await factory()
            self.set(key, value, ttl_s)
            return value
