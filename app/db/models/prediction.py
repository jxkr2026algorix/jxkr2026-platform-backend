"""자체 모델 실행 이력.

**이 표의 값은 어느 기관도 보증하지 않는다.** 산림청 산사태위험등급 1~5 같은 공식 값과
같은 화면에 놓일 때 반드시 구분되어야 해서, 실행 단위로 모델·체크포인트·입력 출처를 남긴다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, JSONType, Timestamped, UUIDPrimaryKey


class PredictionRun(Base, UUIDPrimaryKey, Timestamped):
    __tablename__ = "prediction_runs"
    __table_args__ = (
        Index("ix_prediction_runs_recipe_region", "recipe", "region_code"),
        Index("ix_prediction_runs_requested_at", "requested_at"),
    )

    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="SET NULL")
    )
    recipe: Mapped[str] = mapped_column(String(48), nullable=False)
    region_code: Mapped[str] = mapped_column(String(10), nullable=False)
    hazard: Mapped[str | None] = mapped_column(String(32))
    horizon_minutes: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(String(16), default="succeeded", nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(96))
    model_version: Mapped[str | None] = mapped_column(String(32))
    served_by: Mapped[str | None] = mapped_column(String(32))  # triton | stub
    feature_mode: Mapped[str | None] = mapped_column(String(16))  # real | synthetic
    is_stub: Mapped[bool] = mapped_column(default=False, nullable=False)

    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    error_detail: Mapped[str | None] = mapped_column(Text)

    # 격자 전체가 아니라 화면이 쓰는 요약만 보관한다. 원본 텐서는 ML 서버가 소유한다.
    summary: Mapped[dict | None] = mapped_column(JSONType)
    request_payload: Mapped[dict | None] = mapped_column(JSONType)
