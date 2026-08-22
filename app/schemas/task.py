"""현장 임무·보고 스키마."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TaskStatus = Literal["unassigned", "assigned", "in_progress", "in_review", "done", "cancelled"]


class TaskCreate(BaseModel):
    title: str = Field(max_length=200)
    detail: str | None = None
    community_id: uuid.UUID | None = None
    kind: Literal["verify_household", "verify_route", "assist_transport", "verify", "other"] = (
        "verify"
    )
    priority: int = Field(default=2, ge=1, le=3, description="1이 가장 급하다")
    assignee: str | None = None
    due_at: datetime | None = None


class TaskUpdate(BaseModel):
    status: TaskStatus | None = None
    assignee: str | None = None
    priority: int | None = Field(default=None, ge=1, le=3)
    detail: str | None = None
    due_at: datetime | None = None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    incident_id: uuid.UUID
    community_id: uuid.UUID | None = None
    community_name: str | None = None
    title: str
    detail: str | None = None
    kind: str
    priority: int
    status: str
    assignee: str | None = None
    due_at: datetime | None = None
    created_by: str
    created_at: datetime
    report_count: int = 0


class AccessConstraint(BaseModel):
    kind: Literal["road_closed", "bridge_unsafe", "slope_failure", "flooded", "other"]
    location: str
    detail: str | None = None
    lat: float | None = None
    lon: float | None = None


class ReportCreate(BaseModel):
    body: str = Field(min_length=1)
    observation: Literal[
        "households_verified", "route_blocked", "route_open", "hazard_observed", "other"
    ] = "other"
    households_verified: int | None = Field(default=None, ge=0)
    access_constraints: list[AccessConstraint] = []
    request_replan: bool = Field(
        default=False,
        description="이 보고가 승인된 계획을 무효화하는가. true 면 계획이 재승인 대기로 바뀐다",
    )


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID
    incident_id: uuid.UUID
    body: str
    observation: str
    households_verified: int | None = None
    access_constraints: list[AccessConstraint] = []
    submitted_by: str
    submitted_at: datetime
    triggered_replan: bool
