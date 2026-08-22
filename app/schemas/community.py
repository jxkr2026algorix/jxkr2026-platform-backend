"""마을·대피소 스키마."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class MapPoint(BaseModel):
    """WebGPU 맵 캔버스가 쓰는 0..1 정규화 좌표."""

    x: float
    y: float


class CommunityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    region_code: str
    region_name: str
    name: str
    name_en: str | None = None
    emd_name: str | None = None
    residents: int
    households: int | None = None
    assisted_mobility_estimate: int | None = Field(
        default=None,
        description="SGIS 읍면동 고령인구 기반 **대리지표**. 개인 단위 이동능력이 아니다",
    )
    vulnerability_note: str | None = None
    lat: float | None = None
    lon: float | None = None
    map_point: MapPoint | None = None
    notes: str | None = None
    tags: list[str] = []
    data_mode: str = Field(description="real | synthetic — 합성이면 화면에 훈련 표시 유지")


class CommunityCreate(BaseModel):
    region_code: str
    region_name: str
    name: str
    name_en: str | None = None
    emd_name: str | None = None
    residents: int = 0
    households: int | None = None
    assisted_mobility_estimate: int | None = None
    vulnerability_note: str | None = None
    lat: float | None = None
    lon: float | None = None
    map_point: MapPoint | None = None
    notes: str | None = None
    tags: list[str] = []
    data_mode: str = "synthetic"


class ShelterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    region_code: str
    name: str
    address: str | None = None
    lat: float | None = None
    lon: float | None = None
    capacity: int | None = None
    capacity_basis: str | None = Field(
        default=None,
        description="정원의 근거. annual_file 이면 연 1회 갱신 파일 — 실시간 수용현황이 아니다",
    )
    hazards: list[str] = Field(description="이 시설이 담당하는 재난. 자동 전용 금지")
    facility_type: str | None = None
    manager: str | None = None
    phone: str | None = None
    distance_km: float | None = None
    source_dataset_id: str | None = None
    source_attribution: str | None = None
    data_mode: str = "synthetic"


class ShelterCreate(BaseModel):
    region_code: str
    name: str
    hazards: list[str]
    address: str | None = None
    lat: float | None = None
    lon: float | None = None
    capacity: int | None = None
    capacity_basis: str | None = "annual_file"
    facility_type: str | None = None
    manager: str | None = None
    phone: str | None = None
    source_dataset_id: str | None = None
    source_attribution: str | None = None
    data_mode: str = "synthetic"
