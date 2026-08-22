"""주민 연락 스키마."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ContactResponse = Literal["pending", "evacuating", "support_requested", "refused", "unreachable"]


class ContactStart(BaseModel):
    """승인된 계획을 근거로 연락 대상 명단을 만든다.

    발송 자체는 이 시스템이 하지 않는다 — 명단과 결과 기록만 소유한다.
    """

    channel: Literal["call", "sms", "call_sms", "radio", "door"] = "call_sms"
    note: str | None = None


class ContactUpdate(BaseModel):
    response: ContactResponse
    households_confirmed: int | None = Field(default=None, ge=0)
    follow_up: str | None = None


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    incident_id: uuid.UUID
    plan_id: uuid.UUID | None = None
    community_id: uuid.UUID
    community_name: str | None = None
    households: int
    households_confirmed: int
    channel: str
    response: str
    follow_up: str | None = None
    attempted_at: datetime | None = None
    recorded_by: str | None = None
    data_mode: str


class ContactRollup(BaseModel):
    """연락 현황 집계.

    `unreachable` 을 따로 세는 이유가 있다. 미확인 세대를 전체에 섞으면
    '연락 완료 80%'가 되고, 갈 곳 없는 사람이 그 20% 안에서 보이지 않게 된다.
    """

    total: int = 0
    pending: int = 0
    evacuating: int = 0
    support_requested: int = 0
    refused: int = 0
    unreachable: int = 0
    households_total: int = 0
    households_confirmed: int = 0
    needs_field_verification: int = Field(
        default=0, description="unreachable — 현장 확인 임무로 넘겨야 하는 건수"
    )
