"""주민 연락 시도와 그 결과.

**연락 자체는 이 시스템이 하지 않는다.** 전화·문자 발송은 별도 채널이고, 여기 남는 것은
"누구에게 어떤 경로로 시도했고 무엇을 확인했는가"다. 확인하지 못한 세대를
'연락됨'으로 세면 갈 곳 없는 사람이 명단에서 사라진다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, DataMode, Timestamped, UUIDPrimaryKey

CONTACT_RESPONSES = (
    "pending",  # 아직 시도 안 함
    "evacuating",  # 대피 중 확인
    "support_requested",  # 이동 지원 필요
    "refused",  # 대피 거부
    "unreachable",  # 응답 없음 — 현장 확인 대상
)


class ContactAttempt(Base, UUIDPrimaryKey, Timestamped, DataMode):
    __tablename__ = "contact_attempts"
    __table_args__ = (
        Index("ix_contact_attempts_incident_id", "incident_id"),
        Index("ix_contact_attempts_response", "response"),
    )

    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("evacuation_plans.id", ondelete="SET NULL")
    )
    community_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("communities.id"), nullable=False
    )

    households: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    households_confirmed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    channel: Mapped[str] = mapped_column(String(24), default="call", nullable=False)
    response: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    follow_up: Mapped[str | None] = mapped_column(Text)

    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_by: Mapped[str | None] = mapped_column(String(64))
