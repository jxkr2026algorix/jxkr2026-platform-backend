"""프로세스 내 이벤트 브로커 — SSE 로 나가는 것들의 출처.

콘솔과 모바일이 같은 상황을 보려면 한쪽이 만든 변화가 다른 쪽에 즉시 닿아야 한다.
폴링으로도 되지만 확산 프레임은 초 단위로 나오므로 스트림이어야 한다.

**구독자가 느리면 그 구독자만 버린다.** 큐가 차면 오래된 프레임을 버리고 최신을 남긴다 —
대피 상황에서 30초 전 확산도를 순서대로 보여주는 것보다 지금 것을 보여주는 쪽이 맞다.

지금은 단일 프로세스 메모리다. 워커를 늘리면 Redis pub/sub 로 바꿔야 하고, 그때
바뀌는 것은 이 파일뿐이다.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# 구독자당 큐 길이. 확산 프레임은 크므로 짧게 잡는다.
QUEUE_SIZE = 32
# 프록시가 유휴 연결을 끊지 않도록 보내는 주석 프레임 간격(초).
HEARTBEAT_SECONDS = 15.0


@dataclass(slots=True)
class Event:
    """SSE 한 건."""

    kind: str
    data: dict[str, Any]
    incident_id: str | None = None
    at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def encode(self) -> str:
        payload = {**self.data, "at": self.at.isoformat()}
        if self.incident_id:
            payload["incident_id"] = self.incident_id
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return f"event: {self.kind}\ndata: {body}\n\n"


class EventBroker:
    """구독자에게 이벤트를 나눠 준다."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: Event) -> None:
        """모든 구독자에게 보낸다. 큐가 찬 구독자는 가장 오래된 것을 잃는다."""
        for queue in list(self._subscribers):
            if queue.full():
                # 순서를 지키는 것보다 최신 상태를 주는 쪽이 낫다.
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[Event]]:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    async def stream(
        self, incident_id: str | None = None
    ) -> AsyncIterator[str]:
        """SSE 본문. 하트비트를 섞어 유휴 연결이 끊기지 않게 한다."""
        async with self.subscribe() as queue:
            # 연결 직후 한 번 알려 준다 — 화면이 "연결됨"을 스스로 판단하지 않도록.
            yield Event(kind="stream.open", data={"ok": True}).encode()
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=HEARTBEAT_SECONDS
                    )
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if incident_id and event.incident_id not in (None, incident_id):
                    continue
                yield event.encode()


broker = EventBroker()
