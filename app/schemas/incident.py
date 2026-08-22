"""상황 스키마."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.hazard import Hazard


class IncidentCreate(BaseModel):
    title: str = Field(max_length=200)
    region_code: str
    hazard: Hazard
    level: int = Field(default=1, ge=1, le=3)
    summary: str | None = None
    code: str | None = Field(default=None, description="없으면 YYYY-MMDD-NN 으로 생성")
    opening_evidence: dict[str, Any] | None = Field(
        default=None, description="개시 시점 근거 스냅샷 — 봉투 요약, 예측 id"
    )


class IncidentUpdate(BaseModel):
    title: str | None = None
    level: int | None = Field(default=None, ge=1, le=3)
    status: Literal["open", "monitoring", "closed"] | None = None
    summary: str | None = None


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    title: str
    region_code: str
    region_name: str
    hazard: str
    hazard_korean: str | None = None
    level: int
    status: str
    summary: str | None = None
    declared_by: str
    declared_at: datetime
    closed_at: datetime | None = None
    closed_by: str | None = None
    opening_evidence: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class TimelineEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor: str
    action: str
    entity_type: str
    entity_id: str | None = None
    summary: str
    payload: dict[str, Any] | None = None
    created_at: datetime
