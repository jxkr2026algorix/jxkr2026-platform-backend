"""설정. 환경변수 접두어는 SALGIL_ 이다."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Role = Literal["operator", "approver", "field", "admin"]
ROLES: frozenset[str] = frozenset({"operator", "approver", "field", "admin"})


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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings: Settings = get_settings()
