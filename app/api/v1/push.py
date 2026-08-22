"""주민 기기의 Web Push 구독 등록.

**주민이 쓰는 라우트다.** 상황을 만들 권한은 필요 없고, 있으면 안 된다 — 알림을 받으려고
운영자 키를 들고 다니게 만들면 그 키가 주민 기기 수만큼 복제된다. 여기서 요구하는 것은
`CurrentPrincipal` 뿐이다.

공개키는 브라우저가 구독을 만들 때 필요하고, 그래서 공개다. 비밀키는 어떤 응답에도
실리지 않는다 — 그것을 가진 사람은 이 도메인 이름으로 주민 잠금화면에 무엇이든 띄운다.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.api.deps import Config, CurrentPrincipal, Db
from app.api.route import TransactionalRoute
from app.services import push

router = APIRouter(prefix="/push", tags=["push"], route_class=TransactionalRoute)


class SubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=255)
    auth: str = Field(min_length=1, max_length=255)


class SubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)
    keys: SubscriptionKeys
    # 어느 시군의 알림을 받을지. 비우면 전부 받는다 — QR 로 들어온 청중 화면이 그렇다.
    region_code: str | None = Field(default=None, max_length=10)


class Unsubscribe(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)


@router.get(
    "/key",
    summary="구독에 쓸 VAPID 공개키",
    description=(
        "브라우저가 `pushManager.subscribe` 에 넣을 값이다. "
        "`configured` 가 false 면 서버에 키가 없다는 뜻이고, 화면은 구독을 시도하지 "
        "말고 그 사실을 말해야 한다 — 조용히 실패하면 주민은 알림을 켠 줄 안다."
    ),
)
async def vapid_key(settings: Config, _: CurrentPrincipal) -> dict:
    return {
        "configured": push.configured(settings),
        "public_key": settings.vapid_public_key,
    }


@router.post("/subscribe", summary="이 기기로 알림 받기", status_code=201)
async def subscribe(
    payload: SubscriptionIn,
    session: Db,
    request: Request,
    _: CurrentPrincipal,
) -> dict:
    await push.save(
        session,
        endpoint=payload.endpoint,
        p256dh=payload.keys.p256dh,
        auth=payload.keys.auth,
        region_code=payload.region_code,
        user_agent=request.headers.get("user-agent", "")[:400] or None,
    )
    return {"ok": True}


@router.post("/unsubscribe", summary="이 기기로 알림 그만 받기")
async def unsubscribe(payload: Unsubscribe, session: Db, _: CurrentPrincipal) -> dict:
    await push.forget(session, payload.endpoint)
    return {"ok": True}
