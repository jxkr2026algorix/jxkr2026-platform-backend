"""테스트 픽스처.

상류(GB SafeData, ML 서버)는 **절대 실제로 호출하지 않는다.** 정부 API 호출 한도를
테스트가 갉아먹으면 안 되고, 상류 장애로 CI 가 빨개지면 안 된다.
DB 는 인메모리 SQLite 를 쓴다 — 스키마가 Postgres 전용 타입에 기대지 않는지도 같이 검증된다.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

os.environ.setdefault("SALGIL_ENV", "test")
os.environ["SALGIL_DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["SALGIL_MLENGINE_MODE"] = "stub"
os.environ["SALGIL_API_KEYS"] = "test-op:operator,test-ap:approver,test-fd:field"

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db import models  # noqa: F401 — create_all 전에 모든 테이블을 metadata 에 등록한다
from app.db import session as session_module
from app.db.base import Base


@pytest.fixture
def settings():
    get_settings.cache_clear()
    return get_settings()


@pytest.fixture
async def engine(settings):
    # SQLite 인메모리는 커넥션마다 별도 DB 라서 StaticPool 로 하나를 공유한다.
    from sqlalchemy.pool import StaticPool

    engine = create_async_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def sessionmaker_(engine) -> async_sessionmaker[AsyncSession]:
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    session_module._engine = engine
    session_module._sessionmaker = factory
    return factory


@pytest.fixture
async def session(sessionmaker_) -> AsyncIterator[AsyncSession]:
    async with sessionmaker_() as session:
        yield session


@pytest.fixture
async def client(sessionmaker_, fake_gbsafe) -> AsyncIterator[AsyncClient]:
    from app.clients.mlengine import MlEngineClient
    from app.clients.upstage import UpstageClient
    from app.main import app

    app.state.gbsafe = fake_gbsafe
    app.state.mlengine = MlEngineClient(get_settings())
    # 키 없이 뜬다. 챗봇만 꺼지고 나머지는 영향받지 않는다는 것도 같이 검증된다.
    app.state.upstage = UpstageClient(get_settings())

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": "Bearer test-op"},
    ) as http_client:
        yield http_client


@pytest.fixture
async def seeded(session):
    """마을 4곳과 대피소 3곳."""
    from app.services.seed import seed_demo

    result = await seed_demo(session)
    await session.commit()
    return result


@pytest.fixture
def fake_gbsafe():
    from tests.fakes import FakeGbSafeClient

    return FakeGbSafeClient()
