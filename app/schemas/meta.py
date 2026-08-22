"""메타 — 지역, 재난 가용성, 서비스 상태."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.hazard import HazardCapability


class LatLon(BaseModel):
    lat: float
    lon: float


class KmaGrid(BaseModel):
    nx: int
    ny: int


class Region(BaseModel):
    code: str = Field(description="행정표준코드 5자리 (문경시 = 47280)")
    name: str
    center: LatLon | None = None


class ResolvedRegion(BaseModel):
    """지역명 → 코드·좌표·기상격자.

    기관마다 식별자가 다르다. 기상청은 격자(nx/ny), ASOS 는 지점번호, 나머지는 시군구 코드다.
    **이 변환을 프론트엔드나 다른 레포에 다시 적으면 안 된다.**
    """

    found: bool
    code: str | None = None
    name: str | None = None
    full_name: str | None = None
    center: LatLon | None = None
    kma_grid: KmaGrid | None = None
    asos_station: int | None = None
    caveats: list[str] = []
    message: str | None = None
    available: list[str] | None = None


class CapabilityMatrix(BaseModel):
    hazards: list[HazardCapability]
    ready: list[str] = []
    partial: list[str] = []
    blocked: list[str] = []
    fetched_at: datetime | None = None


class ComponentHealth(BaseModel):
    name: str
    ok: bool
    detail: str | None = None
    latency_ms: float | None = None


class ServiceHealth(BaseModel):
    """준비 상태.

    상류가 죽었다고 이 서비스가 죽은 것은 아니다 — 운영 상태(계획·연락·임무)는 계속
    읽고 쓸 수 있어야 한다. 그래서 구성요소별로 나눠 보고한다.
    """

    status: str = Field(description="ok | degraded | down")
    version: str
    env: str
    components: list[ComponentHealth] = []
    checked_at: datetime
