"""설정. 환경변수 접두어는 SALGIL_ 이다."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Role = Literal["operator", "approver", "field", "admin"]
ROLES: frozenset[str] = frozenset({"operator", "approver", "field", "admin"})

# 저장소에 적혀 있는 값들. 운영에서 이대로 뜨면 아무나 승인·연락 기록을 할 수 있다.
DEV_API_KEYS: frozenset[str] = frozenset({"dev-operator", "dev-approver", "dev-field"})
DEV_DB_PASSWORDS: frozenset[str] = frozenset({"salgil", "postgres", "password", "changeme"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SALGIL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"
    log_json: bool = False

    database_url: str = "postgresql+asyncpg://salgil:salgil@127.0.0.1:5432/salgil"

    # ── Upstage Solar (챗봇) ──────────────────────────────────────────────
    # 키가 없으면 챗봇만 꺼진다. 나머지 기능은 영향을 받지 않는다.
    upstage_api_key: str = ""
    # base URL 과 모델을 설정으로 둔 것은 스펙이 바뀌어도 배포 없이 따라가기 위해서다.
    upstage_base_url: str = "https://api.upstage.ai/v1"
    upstage_model: str = "solar-pro-4"
    # 추론 강도는 응답 시간과 직결된다. 대피 안내는 기다려 주지 않는다.
    upstage_reasoning_effort: str = "low"
    upstage_timeout_s: float = 90.0
    # 한 질문에 허용할 도구 호출 왕복. 모델이 같은 도구를 무한히 부르는 것을 막는다.
    upstage_max_tool_rounds: int = 4
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 20

    gbsafe_base_url: str = "https://datainfra.salgil.gyeongbuk.kr"
    gbsafe_timeout_s: float = 30.0
    gbsafe_context_timeout_s: float = 120.0
    gbsafe_cache_ttl_s: int = 60

    mlengine_mode: Literal["stub", "http"] = "stub"
    mlengine_base_url: str = "http://127.0.0.1:8900"
    mlengine_timeout_s: float = 60.0
    mlengine_api_key: str = ""

    api_keys: str = "dev-operator:operator,dev-approver:approver,dev-field:field"
    cors_origins: str = "http://localhost:8080,http://localhost:8081,http://localhost:8082"

    # OSM 추출본 경로 (GeoJSON 또는 GeoJSONSeq). 비어 있으면 경로 계산이 거절된다.
    # **이 파일을 저장소에 커밋하지 않는다** — OSM 파생물은 ODbL 이고, KOGL 정부
    # 데이터와 병합해 배포하면 share-alike 가 정부 데이터에까지 얹힌다.
    road_network_path: str = ""

    # 상황판이 마을 단위로 말할 수 있는 기본 시군. 데모/시드의 기준점이다.
    default_region_code: str = "47750"  # 청송군 — 프론트엔드 콘솔의 기본 화면

    @field_validator("log_level")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    @property
    def api_key_map(self) -> dict[str, str]:
        """ "키:역할" 쌍을 파싱한다. 역할이 없으면 operator 로 둔다."""
        mapping: dict[str, str] = {}
        for entry in self.api_keys.split(","):
            entry = entry.strip()
            if not entry:
                continue
            key, _, role = entry.partition(":")
            key = key.strip()
            role = role.strip() or "operator"
            if not key:
                continue
            if role not in ROLES:
                raise ValueError(f"알 수 없는 역할 {role!r} — 허용: {sorted(ROLES)}")
            mapping[key] = role
        return mapping

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_key_map)

    @property
    def is_production_like(self) -> bool:
        return self.env in ("staging", "production")

    def unsafe_defaults(self) -> list[str]:
        """운영에 그대로 나가면 안 되는 설정.

        저장소에 적힌 값으로 뜨는 것을 막는다. 경고만 남기면 로그에 묻히고,
        묻힌 채로 배포된다.
        """
        problems: list[str] = []

        if not self.api_key_map:
            problems.append("SALGIL_API_KEYS 가 비어 있습니다 — 인증이 꺼진 채로 뜹니다")
        elif DEV_API_KEYS & set(self.api_key_map):
            problems.append(
                "SALGIL_API_KEYS 에 저장소에 적힌 개발용 키가 들어 있습니다 "
                "(dev-operator 등) — 아무나 계획 승인과 주민 연락 기록을 할 수 있습니다"
            )

        password = self.database_url.partition("://")[2].partition(":")[2].partition("@")[0]
        if password in DEV_DB_PASSWORDS:
            problems.append(
                "SALGIL_DATABASE_URL 의 비밀번호가 기본값입니다 — POSTGRES_PASSWORD 를 바꾸세요"
            )

        if any(origin.startswith("http://localhost") for origin in self.cors_origin_list):
            problems.append(
                "SALGIL_CORS_ORIGINS 에 localhost 가 남아 있습니다 — "
                "운영 프론트엔드 주소로 바꾸세요"
            )

        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()
