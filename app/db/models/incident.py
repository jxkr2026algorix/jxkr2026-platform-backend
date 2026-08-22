"""상황(incident)과 감사 이력."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONType, Timestamped, UUIDPrimaryKey

INCIDENT_STATUSES = ("open", "monitoring", "closed")


class Incident(Base, UUIDPrimaryKey, Timestamped):
    """하나의 재난 대응 단위.

    상황은 사람이 연다. 관측값이 임계를 넘었다는 이유로 시스템이 자동으로 열지 않는다 —
    대피 판단은 사람 몫이고, 이 표는 그 사람이 무엇을 언제 정했는지를 남기는 자리다.
    """

    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_status", "status"),
        Index("ix_incidents_region_code", "region_code"),
    )

    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    region_code: Mapped[str] = mapped_column(String(10), nullable=False)
    region_name: Mapped[str] = mapped_column(String(64), nullable=False)
    hazard: Mapped[str] = mapped_column(String(32), nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)

    summary: Mapped[str | None] = mapped_column(Text)
    declared_by: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(64))

    # 상황 개시 시점에 화면에 떠 있던 근거의 스냅샷 (봉투 요약 + 예측 id).
    # 나중에 "그때 무엇을 보고 정했는가"를 재구성하기 위한 것이다.
    opening_evidence: Mapped[dict | None] = mapped_column(JSONType)

    plans: Mapped[list[EvacuationPlan]] = relationship(  # noqa: F821
        back_populates="incident", cascade="all, delete-orphan", lazy="selectin"
    )


class AuditEvent(Base, UUIDPrimaryKey):
    """무엇이 언제 누구에 의해 바뀌었는가.

    승인·연락개시·보고접수처럼 되돌릴 수 없는 결정은 전부 여기 남는다.
    화면의 타임라인이 이 표를 그대로 읽는다.
    """

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_incident_id_created_at", "incident_id", "created_at"),)

    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE")
    )
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
