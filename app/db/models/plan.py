"""대피계획과 그 항목.

계획은 **버전**을 갖는다. 현장 보고가 들어오면 승인된 계획이 그대로 유지되는 대신
`reapproval_required` 로 바뀌고, 개정본이 새 버전으로 쌓인다. 승인 이력이 지워지지 않아야
"누가 무엇을 승인했는가"에 답할 수 있다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, JSONType, Timestamped, UUIDPrimaryKey

PLAN_STATUSES = ("draft", "approved", "reapproval_required", "superseded")


class EvacuationPlan(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "evacuation_plans"
    __table_args__ = (
        UniqueConstraint("incident_id", "version", name="uq_plan_incident_version"),
        Index("ix_evacuation_plans_incident_id", "incident_id"),
    )

    incident_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)

    rationale: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True))

    # 이 계획을 만들 때 쓴 근거. 관측 봉투 요약, 예측 실행 id, 현장 보고 id.
    # 순서의 근거를 남기지 않으면 "왜 상촌이 먼저인가"에 답할 수 없다.
    evidence: Mapped[dict | None] = mapped_column(JSONType)

    incident: Mapped[Incident] = relationship(back_populates="plans")  # noqa: F821
    items: Mapped[list[PlanItem]] = relationship(
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="PlanItem.order_index",
        lazy="selectin",
    )

    @property
    def is_actionable(self) -> bool:
        """주민 연락을 개시해도 되는 상태인가."""
        return self.status == "approved"


class PlanItem(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "plan_items"
    __table_args__ = (Index("ix_plan_items_plan_id", "plan_id"),)

    plan_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("evacuation_plans.id", ondelete="CASCADE"), nullable=False
    )
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    community_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("communities.id"), nullable=False
    )
    shelter_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("shelters.id")
    )

    residents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    transport: Mapped[str | None] = mapped_column(String(160))
    action: Mapped[str] = mapped_column(String(48), default="prepare", nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    # 이 항목 하나의 근거 — 관측 레코드 지문, 위험등급, 예측 셀 등
    evidence: Mapped[dict | None] = mapped_column(JSONType)

    plan: Mapped[EvacuationPlan] = relationship(back_populates="items")
