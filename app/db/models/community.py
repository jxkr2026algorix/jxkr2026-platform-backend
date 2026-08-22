"""마을과 대피소 — 화면이 마을 단위로 말하기 위한 최소 마스터 데이터."""

from __future__ import annotations

from sqlalchemy import Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, DataMode, JSONType, Timestamped, UUIDPrimaryKey


class Community(Base, UUIDPrimaryKey, Timestamped, DataMode):
    """마을 / 자연부락 단위.

    시군구 단위 예보를 마을 단위 예측처럼 제시하면 안 된다. 이 표는 '어디에 누가 있는가'만
    담고, 위험도는 담지 않는다. 위험도는 상류 관측과 자체 모델에서 그때그때 온다.
    """

    __tablename__ = "communities"
    __table_args__ = (Index("ix_communities_region_code", "region_code"),)

    region_code: Mapped[str] = mapped_column(String(10), nullable=False)
    region_name: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    name_en: Mapped[str | None] = mapped_column(String(128))
    emd_name: Mapped[str | None] = mapped_column(String(64))

    residents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    households: Mapped[int | None] = mapped_column(Integer)
    # SGIS 읍면동 고령인구는 **대리지표**다. 개인 단위 이동능력을 추정하면 안 된다.
    assisted_mobility_estimate: Mapped[int | None] = mapped_column(Integer)
    vulnerability_note: Mapped[str | None] = mapped_column(Text)

    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    # WebGPU 맵 캔버스가 쓰는 0..1 정규화 좌표 (프론트엔드 MapPoint)
    map_x: Mapped[float | None] = mapped_column(Float)
    map_y: Mapped[float | None] = mapped_column(Float)

    notes: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list | None] = mapped_column(JSONType)


class Shelter(Base, UUIDPrimaryKey, Timestamped, DataMode):
    """대피소.

    `hazards` 가 비어 있으면 '아무 재난에나 쓸 수 있다'는 뜻이 **아니다**.
    지진 대피소를 호우·산불 대피소로 자동 전용하면 안 된다. 조회 시 hazard 는 필수다.
    """

    __tablename__ = "shelters"
    __table_args__ = (Index("ix_shelters_region_code", "region_code"),)

    region_code: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    address: Mapped[str | None] = mapped_column(String(255))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)

    # 연 1회 갱신되는 파일에서 온 정원이다. 실시간 수용현황이 아니다.
    capacity: Mapped[int | None] = mapped_column(Integer)
    capacity_basis: Mapped[str | None] = mapped_column(String(64), default="annual_file")

    hazards: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    facility_type: Mapped[str | None] = mapped_column(String(64))
    manager: Mapped[str | None] = mapped_column(String(128))
    phone: Mapped[str | None] = mapped_column(String(64))

    source_dataset_id: Mapped[str | None] = mapped_column(String(32))
    source_attribution: Mapped[str | None] = mapped_column(Text)
