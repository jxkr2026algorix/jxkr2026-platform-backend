"""인증·인가.

해커톤 단계라 정적 API 키를 쓴다. 키는 docker compose 환경변수에서 주입한다.
역할은 운영 절차의 경계와 1:1로 맞춘다 — 승인은 approver 만 할 수 있어야 한다.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Request

from app.core.config import Settings
from app.core.errors import AuthError, ForbiddenError

ROLE_RANK: dict[str, int] = {"field": 1, "operator": 2, "approver": 3, "admin": 4}


@dataclass(frozen=True, slots=True)
class Principal:
    key_id: str
    role: str

    def has_at_least(self, role: str) -> bool:
        return ROLE_RANK.get(self.role, 0) >= ROLE_RANK.get(role, 99)


def _extract_token(request: Request) -> str | None:
    header = request.headers.get("authorization")
    if header:
        scheme, _, token = header.partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            return token.strip()
    api_key = request.headers.get("x-api-key")
    return api_key.strip() if api_key else None


def authenticate(request: Request, settings: Settings) -> Principal:
    if not settings.auth_enabled:
        # 키를 하나도 설정하지 않으면 인증이 꺼진다. 로컬 개발 전용이다.
        return Principal(key_id="anonymous", role="admin")

    token = _extract_token(request)
    if not token:
        raise AuthError("Authorization: Bearer <api-key> 또는 X-API-Key 헤더가 필요합니다")

    for candidate, role in settings.api_key_map.items():
        if hmac.compare_digest(candidate, token):
            return Principal(key_id=candidate[:4] + "…", role=role)

    raise AuthError("알 수 없는 API 키입니다")


def require_role(principal: Principal, role: str) -> None:
    if not principal.has_at_least(role):
        raise ForbiddenError(f"이 작업에는 {role} 권한이 필요합니다 (현재: {principal.role})")
