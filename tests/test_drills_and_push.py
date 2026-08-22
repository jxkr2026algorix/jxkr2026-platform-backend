"""훈련 개시와 주민 푸시 — 실제로 틀렸던 자리들.

여기 있는 것은 전부 데모 중에 잘못 나갔던 것들이다. 모델이 지어낸 지역 코드, 두 번
붙은 훈련 표시, 스키마와 다른 인자 이름. 전부 화면까지 도달한 뒤에야 보였다.
"""

from __future__ import annotations

from app.services import push
from app.services.drills import REGION_NAMES, resolve_region


class TestRegionResolution:
    def test_the_name_wins_over_a_wrong_code(self) -> None:
        """모델은 이름은 되돌려 주지만 다섯 자리 코드는 외우지 못한다.

        실제로 `region_name="봉화군"` 과 `region_code="47250"`(상주시) 을 함께 보냈다.
        코드를 먼저 믿으면 봉화 훈련이 상주에서 열린다.
        """
        assert resolve_region("47250", "봉화군") == ("47920", "봉화군")

    def test_a_bare_code_still_works(self) -> None:
        assert resolve_region("47750", "") == ("47750", "청송군")

    def test_a_shortened_name_resolves(self) -> None:
        assert resolve_region("", "청송") == ("47750", "청송군")

    def test_an_invented_code_falls_back_to_the_name(self) -> None:
        """모델이 실제로 보낸 값이다. 코드가 아니어도 이름이 있으면 개시한다."""
        assert resolve_region("CHEONGSONG", "청송") == ("47750", "청송군")

    def test_outside_gyeongbuk_is_refused(self) -> None:
        """없는 코드로 만들어진 상황은 지도에도 대피소 조회에도 걸리지 않는다."""
        assert resolve_region("11000", "서울특별시") is None

    def test_every_county_resolves_by_its_own_name(self) -> None:
        for code, name in REGION_NAMES.items():
            assert resolve_region("", name) == (code, name)


class TestNotificationText:
    def test_a_drill_title_is_not_marked_twice(self) -> None:
        """챗봇이 만든 제목에는 이미 붙어 있다. 확인하지 않으면 '[훈련] [훈련] ...' 이 된다."""
        title, _ = push._incident_text(
            {"title": "[훈련] 울진군 산불 대응 훈련", "region_name": "울진군", "drill": True}
        )
        assert title == "[훈련] 울진군 산불 대응 훈련"

    def test_a_drill_without_the_marker_gets_one(self) -> None:
        """잠금화면에서 제목만 보고 판단하는 사람이 있다."""
        title, body = push._incident_text(
            {"title": "울진군 산불", "region_name": "울진군", "drill": True}
        )
        assert title.startswith("[훈련]")
        assert "실제 상황이 아닙니다" in body

    def test_a_real_incident_is_never_marked_as_a_drill(self) -> None:
        title, body = push._incident_text(
            {"title": "울진군 산불", "region_name": "울진군", "drill": False}
        )
        assert "훈련" not in title
        assert "훈련" not in body


class TestPushConfiguration:
    def test_both_keys_are_required(self, monkeypatch) -> None:
        """공개키만 있으면 구독은 되고 전송은 안 된다 — 조용히 도달률이 0 이 된다."""
        from app.core.config import Settings

        half = Settings(vapid_public_key="pub", vapid_private_key="")
        assert push.configured(half) is False
        assert push.configured(Settings(vapid_public_key="pub", vapid_private_key="priv")) is True
