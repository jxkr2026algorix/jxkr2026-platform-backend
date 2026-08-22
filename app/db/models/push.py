"""주민 기기의 Web Push 구독.

**여기 없으면 알림도 없다.** SSE 는 화면이 열려 있는 동안만 살아 있다. 새벽 세 시에
산불이 나면 아무도 앱을 보고 있지 않고, 그때 잠긴 화면을 켜는 것은 브라우저 벤더의
푸시 서비스뿐이다. 그 서비스로 보내려면 기기가 준 endpoint 와 두 개의 키가 필요하고,
그것을 보관하는 곳이 이 표다.

`endpoint` 는 기기+브라우저 하나를 가리키는 URL 이고 그 자체가 식별자다. 같은 사람이
앱을 다시 설치하면 새 endpoint 가 오고 옛것은 죽는다 — 죽은 구독으로 보내면 404/410 이
오므로, 그때 지운다. 지우지 않으면 명단이 유령으로 불어나 실제 도달률을 가린다.
"""

from __future__ import annotations

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamped, UUIDPrimaryKey


class PushSubscription(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "push_subscriptions"
    __table_args__ = (Index("ix_push_subscriptions_region_code", "region_code"),)

    # 푸시 서비스 URL. 기기마다 다르고 길다 — 인덱스가 아니라 유니크 제약으로 다룬다.
    endpoint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # 기기의 공개키와 인증 시크릿. 본문 암호화에 둘 다 필요하다.
    p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    auth: Mapped[str] = mapped_column(String(255), nullable=False)
    # 어느 시군의 알림을 받을지. 비어 있으면 전부 받는다 — 데모의 청중 화면이 그렇다.
    region_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
