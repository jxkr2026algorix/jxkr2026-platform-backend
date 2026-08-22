"""상류에서 온 출처 봉투와, 화면이 반드시 지켜야 하는 3상태 계약.

GB SafeData 의 모든 데이터 응답은 하나의 봉투를 쓴다. 그 봉투를 **줄이지 않고** 프론트엔드까지
그대로 전달하는 것이 이 백엔드의 계약이다. `records` 만 꺼내 보내면 다음이 사라진다.

- `complete=false`  일부 원천을 못 읽었다
- `absence_confirmed=false` 빈 결과를 '해당 없음'으로 읽으면 안 된다
- `freshness.usable_for_decision=false` 오래된 값이다
- `source.mode="synthetic"` 훈련 데이터다

그래서 여기에 `state` 를 하나 계산해 붙인다. 프론트엔드는 이 값만 보고 색을 정하면 된다.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DataState(StrEnum):
    """화면 3상태.

    2상태(있음/없음)로 그리면 **장애가 초록 타일로 보인다.** 그것이 이 프로젝트가
    막으려는 사고다.
    """

    DATA = "DATA"  # 값이 있다 — 표시한다
    NONE = "NONE"  # 조회 성공 + 부재 확인 — "발효 중 없음"
    UNVERIFIED = "UNVERIFIED"  # 확인 불가 — 안심시키는 색을 쓰면 안 된다


class UpstreamModel(BaseModel):
    """상류 스키마가 늘어나도 깨지지 않게 여분 필드를 보존한다."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class SourceRef(UpstreamModel):
    dataset_id: str | None = None
    dataset_name: str | None = None
    provider: str | None = None
    license: str | None = None
    license_summary: str | None = None
    attribution: str | None = None
    source_url: str | None = None
    endpoint: str | None = None
    mode: str | None = Field(default=None, description="real | snapshot | synthetic")
    upstream_status: str | None = None
    retrieved_at: datetime | None = None
    observed_at: datetime | None = None
    published_at: datetime | None = None
    snapshot_id: str | None = None
    may_modify: bool | None = None
    may_redistribute: bool | None = None


class Freshness(UpstreamModel):
    status: str | None = None
    age_seconds: float | None = None
    expected_cycle_seconds: float | None = None
    as_of: datetime | None = None
    reason: str | None = None
    usable_for_decision: bool | None = None


class Record(UpstreamModel):
    payload: dict[str, Any] = Field(default_factory=dict)
    source: SourceRef | None = None
    freshness: Freshness | None = None
    quality_flags: list[Any] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    fingerprint: str | None = None


class Citation(UpstreamModel):
    dataset_id: str | None = None
    dataset_name: str | None = None
    provider: str | None = None
    license: str | None = None
    source_url: str | None = None
    as_of: datetime | None = None
    mode: str | None = None
    text: str | None = None


class Receipt(UpstreamModel):
    """원천 하나를 조회한 결과. `failed` 를 지우면 실패가 '없음'이 된다."""

    connector: str | None = None
    dataset_id: str | None = None
    outcome: str | None = Field(default=None, description="records | confirmed_empty | failed")
    record_count: int | None = None
    checked_at: datetime | None = None
    upstream_status: str | None = None
    detail: str | None = None


class Degradation(UpstreamModel):
    dataset_id: str | None = None
    status: str | None = None
    detail: str | None = None
    occurred_at: datetime | None = None
    last_known_good_at: datetime | None = None
    blocks_interpretation: bool | None = None


class Envelope(UpstreamModel):
    """GB SafeData 응답 봉투 + 우리가 계산한 `state`."""

    records: list[Record] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    receipts: list[Receipt] = Field(default_factory=list)
    degradations: list[Degradation] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    complete: bool = False
    absence_confirmed: bool = False
    record_count: int | None = None
    generated_at: datetime | None = None
    modes: list[str] = Field(default_factory=list)

    state: DataState = Field(
        default=DataState.UNVERIFIED,
        description="DATA | NONE | UNVERIFIED — 화면은 이 값으로 색을 정한다",
    )
    failed_sources: list[str] = Field(
        default_factory=list,
        description="읽지 못한 커넥터 이름. 비어 있지 않으면 화면에 사유를 띄운다",
    )

    @classmethod
    def from_upstream(cls, payload: dict[str, Any]) -> Envelope:
        env = cls.model_validate(payload)
        env.state = compute_state(env)
        env.failed_sources = [
            r.connector or r.dataset_id or "unknown"
            for r in env.receipts
            if (r.outcome or "").lower() == "failed"
        ]
        return env

    @property
    def has_stale_records(self) -> bool:
        return any(
            r.freshness is not None and r.freshness.usable_for_decision is False
            for r in self.records
        )


def compute_state(env: Envelope) -> DataState:
    """3상태 판정. 이 함수 하나가 '실패를 안전으로 읽는 사고'를 막는다."""
    if env.records:
        return DataState.DATA
    if env.complete and env.absence_confirmed:
        return DataState.NONE
    return DataState.UNVERIFIED


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class Acknowledgement(BaseModel):
    ok: bool = True
    message: str
