"""대피계획 스키마.

계획은 **제안**이다. 승인은 사람이 한다. 그래서 생성 API 와 승인 API 가 분리돼 있고,
승인에는 approver 권한과 승인자 이름이 필요하다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class PlanItemIn(BaseModel):
    community_id: uuid.UUID
    shelter_id: uuid.UUID | None = None
    residents: int = Field(default=0, ge=0)
    transport: str | None = None
    action: Literal["evacuate_now", "prepare", "monitor", "shelter_in_place"] = "prepare"
    rationale: str | None = None
    evidence: dict[str, Any] | None = None


class PlanItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_index: int
    community_id: uuid.UUID
    community_name: str | None = None
    shelter_id: uuid.UUID | None = None
    shelter_name: str | None = None
    residents: int
    transport: str | None = None
    action: str
    rationale: str | None = None
    evidence: dict[str, Any] | None = None


class PlanCreate(BaseModel):
    items: list[PlanItemIn] = Field(min_length=1, description="배열 순서가 대피 순서다")
    rationale: str | None = Field(
        default=None, description="순서를 이렇게 정한 이유. 비워 두면 나중에 설명할 수 없다"
    )
    evidence: dict[str, Any] | None = None


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    incident_id: uuid.UUID
    version: int
    status: str
    rationale: str | None = None
    created_by: str
    created_at: datetime
    approved_by: str | None = None
    approved_at: datetime | None = None
    superseded_by: uuid.UUID | None = None
    evidence: dict[str, Any] | None = None
    items: list[PlanItemOut] = []
    is_actionable: bool = Field(default=False, description="true 여야 주민 연락을 개시할 수 있다")
    notice: str | None = Field(
        default=None,
        description="계획의 한계. 예: 실시간 도로 통제가 반영되지 않았다",
    )


class PlanApprove(BaseModel):
    approver: str = Field(min_length=1, max_length=64, description="승인한 사람 이름/직위")
    note: str | None = None
    acknowledge_caveats: bool = Field(
        default=False,
        description="한계(미확인 원천·오래된 값)를 확인했다는 명시적 동의",
    )
