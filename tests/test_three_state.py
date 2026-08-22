"""3상태 계약.

이 파일이 지키는 것 하나: **조회 실패가 '위험 없음'으로 읽히지 않는다.**
GB SafeData 가 실제로 겪은 가장 위험한 결함이고, 여기서 회귀하면 화면이 장애를 초록으로 그린다.
"""

from __future__ import annotations

from app.schemas.common import DataState, Envelope
from tests.fakes import (
    ALL_FAILED_ENVELOPE,
    CONFIRMED_EMPTY_ENVELOPE,
    PARTIAL_FAILURE_ENVELOPE,
)


def test_records_present_is_data():
    env = Envelope.from_upstream(PARTIAL_FAILURE_ENVELOPE)
    assert env.state is DataState.DATA


def test_confirmed_empty_is_none():
    env = Envelope.from_upstream(CONFIRMED_EMPTY_ENVELOPE)
    assert env.state is DataState.NONE
    assert env.complete and env.absence_confirmed


def test_all_sources_failed_is_unverified_not_none():
    """빈 records + 조회 실패 = UNVERIFIED. 절대 NONE 이 아니다."""
    env = Envelope.from_upstream(ALL_FAILED_ENVELOPE)
    assert env.state is DataState.UNVERIFIED
    assert env.state is not DataState.NONE


def test_incomplete_without_absence_is_unverified():
    payload = dict(CONFIRMED_EMPTY_ENVELOPE, complete=True, absence_confirmed=False)
    assert Envelope.from_upstream(payload).state is DataState.UNVERIFIED


def test_failed_sources_are_named():
    env = Envelope.from_upstream(PARTIAL_FAILURE_ENVELOPE)
    assert env.failed_sources == ["landslide_forecast"]


def test_envelope_preserves_provenance():
    """봉투를 줄이지 않는다 — 출처·영수증·저하 사유가 살아 있어야 한다."""
    env = Envelope.from_upstream(PARTIAL_FAILURE_ENVELOPE)
    assert env.records[0].source is not None
    assert env.records[0].source.provider == "기상청"
    assert env.citations and env.citations[0].dataset_id == "15000415"
    assert any(r.outcome == "failed" for r in env.receipts)
    assert env.degradations[0].blocks_interpretation is True


def test_unknown_upstream_fields_survive():
    """상류가 필드를 늘려도 버리지 않는다."""
    payload = dict(PARTIAL_FAILURE_ENVELOPE, brand_new_field={"a": 1})
    env = Envelope.from_upstream(payload)
    assert env.model_dump().get("brand_new_field") == {"a": 1}


def test_stale_records_are_flagged():
    payload = {
        **CONFIRMED_EMPTY_ENVELOPE,
        "records": [
            {
                "payload": {},
                "freshness": {"status": "stale", "usable_for_decision": False},
            }
        ],
    }
    env = Envelope.from_upstream(payload)
    assert env.has_stale_records is True


def test_headline_caveat_leads_with_failure():
    """못 읽은 원천이 있으면 그게 첫 문장이어야 한다 — 오래된 값보다 위험하다."""
    from app.services.situation import headline_caveat

    env = Envelope.from_upstream(PARTIAL_FAILURE_ENVELOPE)
    caveat = headline_caveat(env, None)
    assert caveat is not None
    assert "landslide_forecast" in caveat
    assert "위험 없음" in caveat
