"""SALGIL 플랫폼 백엔드.

경계는 이렇다.

    프론트엔드 (console / mobile / map)
            │  REST
            ▼
    ┌───────────────────────────────┐
    │  이 서비스 — 운영 상태를 소유   │  EC2
    │  상황·계획·승인·연락·임무·보고   │
    └──┬─────────────┬──────────────┘
       │             │
    Postgres    ┌────┴──────────────┐
                │                   │
        GB SafeData (읽기 전용)   ML 추론 서버 (Triton, H100)
        공공데이터 + 출처         자체 예측 모델

**공공데이터는 저장하지 않는다.** 관측·특보·대피소 원본은 GB SafeData 가 소유하고,
여기서는 조회해 그대로 전달한다. 이 서비스가 소유하는 것은 사람이 내린 결정과 그 이력이다.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1.router import api_router
from app.clients.gbsafe import GbSafeClient
from app.clients.mlengine import MlEngineClient
from app.clients.upstage import UpstageClient
from app.core.config import get_settings
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine
from app.schemas.meta import ComponentHealth, ServiceHealth

settings = get_settings()
configure_logging(settings)
log = get_logger(__name__)

DESCRIPTION = """
경북 재난대피 플랫폼 백엔드.

### 화면이 반드시 지켜야 하는 것 — 3상태

관측 응답에는 `state` 가 실린다. **2상태(있음/없음)로 그리면 장애가 초록 타일로 보인다.**

| state | 뜻 | 화면 |
| --- | --- | --- |
| `DATA` | 값이 있다 | 표시 |
| `NONE` | 조회 성공 + 부재 확인 | "발효 중 없음" |
| `UNVERIFIED` | 확인 불가 | **안심시키는 색 금지** |

### 데이터의 출처가 셋이다

- **GB SafeData** — 공공데이터. 출처·라이선스·신선도가 레코드마다 붙는다
- **자체 예측 모델** — `is_derived=true`. 어느 기관도 보증하지 않는다
- **운영 상태** — 이 서비스가 소유. 계획·승인·연락·보고

같은 화면에 놓일 때 셋을 구분하지 않으면, 모델 추정값이 공식 특보처럼 읽힌다.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.gbsafe = GbSafeClient(settings)
    app.state.mlengine = MlEngineClient(settings)
    app.state.upstage = UpstageClient(settings)
    log.info(
        "startup",
        env=settings.env,
        version=__version__,
        gbsafe=settings.gbsafe_base_url,
        mlengine_mode=settings.mlengine_mode,
        mlengine=settings.mlengine_base_url if settings.mlengine_mode == "http" else None,
        auth_enabled=settings.auth_enabled,
    )
    problems = settings.unsafe_defaults()
    if problems:
        if settings.is_production_like:
            # 뜨지 않는 편이 낫다. 저장소에 적힌 키로 운영에 나가면 아무나
            # 계획을 승인할 수 있고, 그건 로그 경고로 막을 수 있는 종류가 아니다.
            joined = "\n  - ".join(problems)
            raise RuntimeError(
                f"SALGIL_ENV={settings.env} 인데 안전하지 않은 기본값이 있습니다:"
                f"\n  - {joined}\n"
                "로컬에서 이대로 띄우려면 SALGIL_ENV=local 로 두세요."
            )
        for problem in problems:
            log.warning("unsafe_default", detail=problem)
    try:
        yield
    finally:
        await app.state.gbsafe.aclose()
        await app.state.mlengine.aclose()
        await app.state.upstage.aclose()
        await dispose_engine()
        log.info("shutdown")


app = FastAPI(
    title="SALGIL Platform Backend",
    version=__version__,
    description=DESCRIPTION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["x-request-id"],
)

install_exception_handlers(app)
app.include_router(api_router)


@app.middleware("http")
async def request_context(request: Request, call_next) -> Response:
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(request_id=request_id, path=request.url.path)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        structlog.contextvars.clear_contextvars()
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["x-request-id"] = request_id
    if request.url.path not in ("/healthz", "/readyz"):
        log.info(
            "request",
            method=request.method,
            status=response.status_code,
            elapsed_ms=elapsed_ms,
            request_id=request_id,
        )
    return response


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "name": "SALGIL Platform Backend",
        "version": __version__,
        "docs": "/docs",
        "openapi": "/openapi.json",
        "api": "/api/v1",
        "upstreams": {
            "gbsafedata": settings.gbsafe_base_url,
            "mlengine": settings.mlengine_base_url if settings.mlengine_mode == "http" else "stub",
        },
    }


@app.get("/healthz", tags=["ops"], summary="살아 있는가")
async def healthz() -> dict:
    return {"status": "ok", "version": __version__}


@app.get(
    "/readyz",
    tags=["ops"],
    response_model=ServiceHealth,
    summary="준비되었는가 — 구성요소별",
    description=(
        "상류가 죽어도 이 서비스는 degraded 로 답한다. 운영 상태(계획·연락·임무)는 "
        "상류 없이도 읽고 쓸 수 있어야 한다."
    ),
)
async def readyz(request: Request) -> ServiceHealth:
    components: list[ComponentHealth] = []

    db_ok, db_detail = await _check_db()
    components.append(ComponentHealth(name="database", ok=db_ok, detail=db_detail))

    gb_ok, gb_detail, gb_latency = await request.app.state.gbsafe.ping()
    components.append(
        ComponentHealth(name="gbsafedata", ok=gb_ok, detail=gb_detail, latency_ms=gb_latency)
    )

    ml_ok, ml_detail, ml_latency = await request.app.state.mlengine.ping()
    components.append(
        ComponentHealth(name="mlengine", ok=ml_ok, detail=ml_detail, latency_ms=ml_latency)
    )

    if not db_ok:
        status = "down"  # DB 가 없으면 아무 결정도 기록할 수 없다
    elif not (gb_ok and ml_ok):
        status = "degraded"
    else:
        status = "ok"

    return ServiceHealth(
        status=status,
        version=__version__,
        env=settings.env,
        components=components,
        checked_at=datetime.now(UTC),
    )


async def _check_db() -> tuple[bool, str | None]:
    from sqlalchemy import text

    from app.db.session import get_sessionmaker

    try:
        factory = get_sessionmaker()
        async with factory() as session:
            await session.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:300]
