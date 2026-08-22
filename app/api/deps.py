"""FastAPI 의존성."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated
from urllib.parse import unquote

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.gbsafe import GbSafeClient
from app.clients.mlengine import MlEngineClient
from app.core.config import Settings, get_settings
from app.core.security import Principal, authenticate, require_role
from app.db.session import session_scope


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """세션을 만들고 요청에 얹는다 — 커밋은 TransactionalRoute 가 한다."""
    async for session in session_scope():
        request.state.db_session = session
        yield session


def settings_dep() -> Settings:
    return get_settings()


def gbsafe_client(request: Request) -> GbSafeClient:
    """앱 수명주기 동안 하나만 산다 — 연결 풀을 재사용하기 위해서다."""
    return request.app.state.gbsafe


def mlengine_client(request: Request) -> MlEngineClient:
    return request.app.state.mlengine


def principal(request: Request, settings: Annotated[Settings, Depends(settings_dep)]) -> Principal:
    return authenticate(request, settings)


CurrentPrincipal = Annotated[Principal, Depends(principal)]
Db = Annotated[AsyncSession, Depends(db_session)]
GbSafe = Annotated[GbSafeClient, Depends(gbsafe_client)]
MlEngine = Annotated[MlEngineClient, Depends(mlengine_client)]
Config = Annotated[Settings, Depends(settings_dep)]


def _role_dep(role: str):
    def _check(current: CurrentPrincipal) -> Principal:
        require_role(current, role)
        return current

    return _check


RequireOperator = Annotated[Principal, Depends(_role_dep("operator"))]
RequireApprover = Annotated[Principal, Depends(_role_dep("approver"))]
RequireField = Annotated[Principal, Depends(_role_dep("field"))]


def actor_name(request: Request, current: Principal) -> str:
    """행위자 표기.

    감사 이력에 'operator' 만 남으면 누가 승인했는지 알 수 없다. 프론트엔드가 담당자
    이름을 `X-Actor` 로 실어 보내면 그 값을 쓴다.

    **한글 이름은 퍼센트 인코딩해서 보내야 한다.** HTTP 헤더 값은 latin-1 이라
    "김과장"을 그대로 넣으면 클라이언트에서 인코딩 오류가 난다.

        X-Actor: %EA%B9%80%EA%B3%BC%EC%9E%A5        // encodeURIComponent("김과장")

    ASCII 이름은 인코딩하지 않아도 그대로 통과한다.
    """
    header = request.headers.get("x-actor")
    if header and header.strip():
        raw = header.strip()
        try:
            decoded = unquote(raw, errors="strict")
        except UnicodeDecodeError:
            decoded = raw
        return decoded[:64]
    return f"{current.role}:{current.key_id}"
