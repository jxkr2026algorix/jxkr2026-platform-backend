"""Web Push — 화면이 닫혀 있어도 도착하는 알림.

SSE 는 탭이 살아 있는 동안만이다. 새벽에 산불이 나면 아무도 앱을 보고 있지 않고, 그때
잠긴 화면을 켜는 것은 브라우저 벤더의 푸시 서비스뿐이다. 이 모듈이 그쪽으로 보낸다.

**보낼 가치가 있는 것만 보낸다.** 상황 발령과 대피 경로가 막힌 것, 둘뿐이다. 확산
프레임까지 밀면 사람들은 알림을 끄고, 그다음 진짜 경보도 같이 사라진다.

전송은 스레드에서 돈다 — `pywebpush` 는 동기 라이브러리이고, 구독자가 수백이면 이벤트
루프가 그동안 멈춘다. 그 루프가 SSE 와 확산 계산을 함께 돌리고 있다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pywebpush import WebPushException, webpush
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.models.push import PushSubscription
from app.db.session import get_sessionmaker
from app.services import drills, events

logger = logging.getLogger(__name__)

# 구독이 죽었다는 뜻. 그 밖의 실패는 일시적일 수 있으므로 지우지 않는다.
GONE_STATUSES = frozenset({404, 410})


def configured(settings: Settings) -> bool:
    return bool(settings.vapid_public_key and settings.vapid_private_key)


async def save(
    session: AsyncSession,
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    region_code: str | None,
    user_agent: str | None,
) -> None:
    """구독을 저장한다. 같은 기기가 다시 구독하면 키만 갱신한다.

    브라우저는 키를 조용히 회전시킨다. 그때 새 행을 만들면 옛 행으로 보낸 알림이 조용히
    실패하고, 명단은 늘었는데 도달은 줄어든다.
    """
    stmt = pg_insert(PushSubscription).values(
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth,
        region_code=region_code,
        user_agent=user_agent,
    )
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=[PushSubscription.endpoint],
            set_={
                "p256dh": stmt.excluded.p256dh,
                "auth": stmt.excluded.auth,
                "region_code": stmt.excluded.region_code,
                "user_agent": stmt.excluded.user_agent,
            },
        )
    )


async def forget(session: AsyncSession, endpoint: str) -> None:
    await session.execute(delete(PushSubscription).where(PushSubscription.endpoint == endpoint))


def _send_one(settings: Settings, row: dict[str, str], payload: str) -> int | None:
    """한 기기로 보낸다. 죽은 구독이면 그 상태 코드를, 아니면 None 을 준다."""
    try:
        webpush(
            subscription_info={
                "endpoint": row["endpoint"],
                "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
            },
            data=payload,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject},
            timeout=settings.push_timeout_s,
            ttl=600,
        )
    except WebPushException as exc:
        status = getattr(exc.response, "status_code", None)
        if status in GONE_STATUSES:
            return status
        logger.warning("push failed (%s): %s", status, str(exc)[:200])
    except Exception as exc:  # 네트워크·DNS·타임아웃
        logger.warning("push failed: %s", str(exc)[:200])
    return None


async def broadcast(
    settings: Settings,
    *,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    region_code: str | None = None,
    tag: str = "salgil-incident",
) -> int:
    """구독자 전원에게 보내고, 도달한 수를 준다.

    `region_code` 를 주면 그 시군을 구독한 기기와 시군을 고르지 않은 기기에만 간다.
    시군을 고르지 않은 기기는 데모의 청중 화면이다 — 어디서 일어나든 봐야 한다.
    """
    if not configured(settings):
        logger.info("push not configured; skipping broadcast")
        return 0

    sessionmaker = get_sessionmaker(settings)
    async with sessionmaker() as session:
        stmt = select(PushSubscription)
        if region_code:
            stmt = stmt.where(
                (PushSubscription.region_code == region_code)
                | (PushSubscription.region_code.is_(None))
            )
        rows = [
            {"endpoint": s.endpoint, "p256dh": s.p256dh, "auth": s.auth}
            for s in (await session.scalars(stmt)).all()
        ]
    if not rows:
        return 0

    payload = json.dumps(
        {"title": title, "body": body, "tag": tag, "data": data or {}},
        ensure_ascii=False,
    )
    results = await asyncio.gather(
        *(asyncio.to_thread(_send_one, settings, row, payload) for row in rows)
    )

    dead = [row["endpoint"] for row, status in zip(rows, results, strict=True) if status]
    if dead:
        # 죽은 구독을 남기면 명단이 유령으로 불어나 실제 도달률을 가린다.
        async with sessionmaker() as session:
            await session.execute(
                delete(PushSubscription).where(PushSubscription.endpoint.in_(dead))
            )
            await session.commit()
        logger.info("pruned %d dead push subscriptions", len(dead))

    delivered = len(rows) - len(dead)
    logger.info("push delivered to %d/%d devices", delivered, len(rows))
    return delivered


# ── 상황 발령을 잠금화면까지 ───────────────────────────────────────────────

# 잠금화면을 켤 만한 것만. 확산 프레임까지 밀면 사람들이 알림을 끄고, 그다음 진짜 경보도
# 같이 사라진다.
PUSH_WORTHY = frozenset({"incident.declared", "route.blocked"})

# 실행 중인 전송을 붙잡아 둔다. 참조가 없으면 파이썬이 전송 도중에 태스크를 수거하고,
# 알림은 조용히 사라진다 — 실패 로그도 없이.
_in_flight: set[asyncio.Task[int]] = set()


def _incident_text(data: dict[str, Any]) -> tuple[str, str]:
    title = str(data.get("title") or "긴급 상황")
    region = str(data.get("region_name") or "")
    if not data.get("drill"):
        return title, f"{region} 안내를 확인하고 대피 경로를 따르세요."
    # 잠금화면에서 제목만 보고 판단하는 사람이 있다. 훈련이라는 말이 첫 단어여야 한다 —
    # 다만 챗봇이 만든 제목에는 이미 붙어 있어, 확인하지 않으면 "[훈련] [훈련] ..." 이 된다.
    if not title.startswith(drills.DRILL_TITLE_PREFIX):
        title = f"{drills.DRILL_TITLE_PREFIX} {title}"
    return title, f"{region} 대응 훈련입니다. 실제 상황이 아닙니다."


def register(settings: Settings) -> None:
    """커밋된 이벤트를 보고 푸시를 보낸다.

    브로커 훅은 동기 컨텍스트에서 불린다 — 커밋이 끝나는 자리다. 전송은 태스크로 떼어
    낸다. 여기서 기다리면 구독자 수만큼 커밋이 늦어지고, 그 커밋은 요청 하나를 붙잡고
    있다.
    """
    if not configured(settings):
        logger.info("VAPID keys are not set; residents get no background alerts")
        return

    def hook(event: Any) -> None:
        if event.kind not in PUSH_WORTHY:
            return
        title, body = _incident_text(event.data)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 이벤트 루프 밖에서 커밋됐다 — 스크립트나 테스트다. 조용히 넘긴다.
            return
        task = loop.create_task(
            broadcast(
                settings,
                title=title,
                body=body,
                data={
                    "incident_id": event.data.get("incident_id"),
                    "drill": bool(event.data.get("drill")),
                },
                region_code=event.data.get("region_code"),
            )
        )
        _in_flight.add(task)
        task.add_done_callback(_in_flight.discard)

    events.on_published(hook)
