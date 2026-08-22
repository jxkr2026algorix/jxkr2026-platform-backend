"""SQLAlchemy 선언적 기반과 공통 컬럼 타입."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

# 마이그레이션 이름이 결정론적이도록 명명 규칙을 고정한다.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Postgres 에서는 JSONB, 테스트용 SQLite 에서는 JSON 으로 내려간다.
JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKey:
    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class DataMode:
    """실데이터인지 훈련용 합성데이터인지.

    주민 연락처·대피 확인·현장 통제는 공개 데이터로 얻을 수 없다. 훈련모드에서
    합성 데이터로 대체하되, 실시간 화면에서 실제 자료처럼 섞이면 안 된다.
    그 구분을 행 단위로 들고 다닌다.
    """

    data_mode: Mapped[str] = mapped_column(String(16), default="synthetic", nullable=False)
