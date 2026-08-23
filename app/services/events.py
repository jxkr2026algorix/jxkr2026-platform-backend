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
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session

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

    async def stream(self, incident_id: str | None = None) -> AsyncIterator[str]:
        """SSE 본문. 하트비트를 섞어 유휴 연결이 끊기지 않게 한다."""
        async with self.subscribe() as queue:
            # 연결 직후 한 번 알려 준다 — 화면이 "연결됨"을 스스로 판단하지 않도록.
            yield Event(kind="stream.open", data={"ok": True}).encode()
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if incident_id and event.incident_id not in (None, incident_id):
                    continue
                yield event.encode()


broker = EventBroker()


# ── 커밋된 뒤에 알리기 ────────────────────────────────────────────────────
#
# 발표와 저장이 어긋나면 두 가지 중 하나가 난다. 커밋 전에 알리면 롤백된 상황이 주민
# 화면에 뜨고, 알리지 않고 커밋만 하면 저장은 됐는데 아무도 모른다. 둘 다 겪었다.
#
# 그래서 이벤트를 세션에 얹어 두고 커밋이 끝난 뒤에 내보낸다. 롤백되면 버린다.
# 스트리밍 응답 안에서 도는 코드도 이 규칙을 그대로 따른다 — 라우트의 트랜잭션 경계가
# 언제 끝나는지 각 호출자가 알 필요가 없어진다.

_PENDING_KEY = "salgil_pending_events"


def publish_after_commit(session: Any, event: Event) -> None:
    """이 세션의 커밋이 성공하면 이벤트를 내보낸다."""
    info = getattr(session, "sync_session", session).info
    info.setdefault(_PENDING_KEY, []).append(event)


@sa_event.listens_for(Session, "after_commit")
def _publish_pending(session: Session) -> None:
    for event in session.info.pop(_PENDING_KEY, []):
        broker.publish(event)
        for hook in _after_publish:
            try:
                hook(event)
            except Exception:  # 알림 실패가 커밋을 되돌리게 두지 않는다
                logger.exception("post-commit hook failed for %s", event.kind)


@sa_event.listens_for(Session, "after_rollback")
def _drop_pending(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


# 커밋된 이벤트를 보고 싶은 쪽이 등록한다. 지금은 Web Push 하나다.
_after_publish: list[Callable[[Event], None]] = []


def on_published(hook: Callable[[Event], None]) -> None:
    _after_publish.append(hook)
