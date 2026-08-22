"""비동기 엔진과 세션 팩토리."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.config import settings as default_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _engine_kwargs(settings: Settings) -> dict:
    if settings.database_url.startswith("sqlite"):
        # 테스트용. 풀 인자를 받지 않는다.
        return {"echo": settings.db_echo}
    return {
        "echo": settings.db_echo,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_pre_ping": True,
        "pool_recycle": 1800,
    }


def get_engine(settings: Settings = default_settings) -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.database_url, **_engine_kwargs(settings))
    return _engine


def get_sessionmaker(settings: Settings = default_settings) -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(settings),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def session_scope() -> AsyncIterator[AsyncSession]:
    """요청 하나가 트랜잭션 하나다.

    **여기서 커밋하지 않는다.** yield 의존성의 정리 코드는 응답을 보낸 뒤에 돌아서,
    커밋이 늦으면 클라이언트의 read-after-write 가 깨진다. 커밋은
    `app.api.route.TransactionalRoute` 가 응답을 내보내기 직전에 한다.
    """
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def reset_engine_for_tests(settings: Settings) -> None:
    global _engine, _sessionmaker
    _engine = create_async_engine(settings.database_url, **_engine_kwargs(settings))
    _sessionmaker = async_sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
