"""현장 임무와 현장 보고.

현장 보고는 계획을 되돌린다. 도로가 막혔다는 보고가 들어왔는데 승인된 계획이
그대로 '승인됨'으로 남아 있으면, 화면은 이미 틀린 계획을 계속 옳다고 말한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONType, Timestamped, UUIDPrimaryKey

TASK_STATUSES = ("unassigned", "assigned", "in_progress", "in_review", "done", "cancelled")


class FieldTask(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "field_tasks"
    __table_args__ = (Index("ix_field_tasks_incident_id_status", "incident_id", "status"),)

    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    community_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("communities.id")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(32), default="verify", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="unassigned", nullable=False)
    assignee: Mapped[str | None] = mapped_column(String(64))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)

    reports: Mapped[list[FieldReport]] = relationship(
        back_populates="task", cascade="all, delete-orphan", lazy="selectin"
    )


class FieldReport(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "field_reports"
    __table_args__ = (Index("ix_field_reports_incident_id", "incident_id"),)

    task_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("field_tasks.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    observation: Mapped[str] = mapped_column(String(32), default="other", nullable=False)
    # 현장에서 확인한 통제 구간·접근 제약. 검증되지 않은 경로를 공식 안전경로로 쓰지 않는다.
    access_constraints: Mapped[list | None] = mapped_column(JSONType)
    households_verified: Mapped[int | None] = mapped_column(Integer)
    submitted_by: Mapped[str] = mapped_column(String(64), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 이 보고가 계획 재승인을 유발했는가
    triggered_replan: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    task: Mapped[FieldTask] = relationship(back_populates="reports")
