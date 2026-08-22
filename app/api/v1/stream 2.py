"""실시간 스트림 — 콘솔과 모바일이 같은 상황을 보게 하는 통로.

폴링으로는 확산을 못 보여준다. 프레임이 초 단위로 나오는데 5초마다 묻는 화면은 그
사이를 건너뛰고, 콘솔과 모바일이 서로 다른 시점을 그린다. 대피 경로를 그 위에서
계산하는 이상 두 화면은 같은 값을 봐야 한다.

**여기서 나가는 예측 프레임은 자체 모델 산출값이다.** `is_derived` 를 프레임마다
싣는 이유는 화면에서 공식 위험등급과 섞이지 않게 하기 위해서다.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import CurrentPrincipal, Db, MlEngine, RequireOperator
from app.services import spread
from app.services.events import Event, broker

router = APIRouter(prefix="/stream", tags=["stream"])


class RenderState(BaseModel):
    """한 화면이 지금 무엇을 보고 있는지. 다른 화면이 따라올 수 있게 공유한다."""

    district_code: str | None = Field(default=None, description="행정표준코드")
    scenario: str | None = None
    view_mode: str | None = Field(default=None, description="flat | tilted")
    incident_id: uuid.UUID | None = None
    playing: bool | None = None
    horizon_minutes: int | None = None
    source: str = Field(default="console", description="console | mobile")


class SpreadRequest(BaseModel):
    hazard: str
    lat: float
    lon: float
    incident_id: uuid.UUID | None = None
    region_code: str | None = None
    size_m: float = Field(default=12_000, ge=2_000, le=60_000)
    horizons_minutes: list[int] | None = None


@router.get(
    "",
    summary="상황 스트림 (SSE)",
    description=(
        "이벤트 종류: `stream.open`, `render.state`, `prediction.frame`, "
        "`spread.complete`, `incident.declared`.\n\n"
        "`prediction.frame` 의 `values_b64` 는 float32 리틀엔디언 배열을 base64 로 "
        "감싼 것이다. row-major 이고 북쪽 행이 먼저다 — 프론트의 "
        "`map:set-hazard-field` 와 같은 순서다.\n\n"
        "느린 구독자는 오래된 프레임을 잃는다. 대피 상황에서 30초 전 확산도를 순서대로 "
        "보여주는 것보다 지금 것을 보여주는 쪽이 맞다."
    ),
)
async def situation_stream(
    request: Request,
    _: CurrentPrincipal,
    incident_id: uuid.UUID | None = Query(
        default=None, description="주면 그 상황의 이벤트만 받는다"
    ),
) -> StreamingResponse:
    async def body():
        async for chunk in broker.stream(
            str(incident_id) if incident_id else None
        ):
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # nginx 가 SSE 를 버퍼링하면 프레임이 뭉쳐서 도착한다.
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/render-state",
    status_code=202,
    summary="내 화면 상태를 공유한다",
    description="구역·시나리오·2D/3D 를 다른 화면이 따라올 수 있게 방송한다.",
)
async def publish_render_state(
    payload: RenderState, _: RequireOperator
) -> dict:
    broker.publish(
        Event(
            kind="render.state",
            data=payload.model_dump(mode="json", exclude_none=True),
            incident_id=str(payload.incident_id) if payload.incident_id else None,
        )
    )
    return {"accepted": True, "subscribers": broker.subscriber_count}


@router.post(
    "/spread",
    status_code=202,
    summary="확산 계산을 시작한다",
    description=(
        "시점별로 추론해 `prediction.frame` 을 순서대로 발행한다. 요청은 바로 돌아오고 "
        "프레임은 스트림으로 온다 — 전부 끝나고 한 번에 주면 그 사이 화면이 비어 있다."
    ),
)
async def start_spread(
    payload: SpreadRequest,
    session: Db,
    client: MlEngine,
    background: BackgroundTasks,
    _: RequireOperator,
) -> dict:
    horizons = (
        tuple(payload.horizons_minutes)
        if payload.horizons_minutes
        else spread.DEFAULT_HORIZONS
    )
    background.add_task(
        spread.run_spread,
        session,
        client,
        hazard=payload.hazard,
        lat=payload.lat,
        lon=payload.lon,
        incident_id=str(payload.incident_id) if payload.incident_id else None,
        region_code=payload.region_code,
        size_m=payload.size_m,
        horizons=horizons,
    )
    return {"accepted": True, "horizons_minutes": list(horizons)}
