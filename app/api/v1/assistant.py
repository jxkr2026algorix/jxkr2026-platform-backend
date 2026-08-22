"""챗봇 연동 프록시.

GB SafeData 는 도구 정의와 시스템 프롬프트를 HTTP 로 준다. 이 백엔드가 중계하는 이유는
정부 인증키가 상류에 있어서 브라우저가 직접 부르면 안 되기 때문이다.

**시스템 프롬프트를 빼고 도구만 붙이면 사고가 난다.** 모델은 기본적으로 도움이 되려 하고,
산사태 조회가 403 으로 실패해 결과가 비면 "산사태 위험 없습니다"라고 답한다.
그래서 프롬프트를 복붙하지 않고 매번 상류에서 받아 쓴다.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import Config, CurrentPrincipal, GbSafe, Upstage
from app.api.route import TransactionalRoute
from app.clients.upstage import UpstageNotConfigured
from app.services import assistant as assistant_service

router = APIRouter(prefix="/assistant", tags=["assistant"], route_class=TransactionalRoute)


@router.get(
    "/system-prompt",
    summary="도구를 안전하게 쓰기 위한 시스템 프롬프트",
    description="복붙하지 말고 이 엔드포인트에서 받아 쓴다. 상류가 고치면 자동으로 따라온다.",
)
async def system_prompt(client: GbSafe, _: CurrentPrincipal) -> Any:
    return await client.agent_system_prompt()


@router.get(
    "/tools",
    summary="OpenAI 호환 도구 정의 12종",
    description=(
        "function calling 스키마를 그대로 LLM 에 넘길 수 있다. "
        "접두어 `gbsafe_` 가 이름의 일부다 — 빼면 '그런 도구 없음'이 난다."
    ),
)
async def tools(client: GbSafe, _: CurrentPrincipal) -> Any:
    return await client.tools()


@router.get(
    "/tools/{name}",
    summary="도구 실행 (조회 전용)",
    description=(
        "질의문자열 인자가 그대로 상류로 전달된다. 쓰기 라우트가 없다는 보장을 위해 GET 이다."
    ),
)
async def call_tool(name: str, request: Request, client: GbSafe, _: CurrentPrincipal) -> Any:
    params = {k: v for k, v in request.query_params.items()}
    return await client.call_tool(name, params)


class ChatTurn(BaseModel):
    role: str = Field(description="user | assistant")
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatTurn] = Field(min_length=1)


@router.post(
    "/chat",
    summary="챗봇 대화 (SSE)",
    description=(
        "Upstage Solar 가 GB SafeData 도구를 써서 답한다. 루프가 여기 있는 이유는 정부 "
        "인증키가 상류에 있어 브라우저가 도구를 직접 부르면 안 되기 때문이고, 도구 실패를 "
        "모델이 '위험 없음'으로 바꾸지 못하게 하기 위해서다.\n\n"
        "이벤트: `tool`(도구 실행 중), `notice`, `delta`(응답 조각), `done`, `error`."
    ),
)
async def chat(
    payload: ChatRequest,
    client: GbSafe,
    upstage: Upstage,
    settings: Config,
    _: CurrentPrincipal,
) -> StreamingResponse:
    history = [turn.model_dump() for turn in payload.messages]

    async def body():
        try:
            async for event in assistant_service.converse(upstage, client, settings, history):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except UpstageNotConfigured as exc:
            # 키가 없다는 사실을 화면까지 올린다. 조용한 빈 답은 '위험 없음'으로 읽힌다.
            yield f"data: {json.dumps({'kind': 'error', 'text': str(exc)}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield (
                "data: "
                + json.dumps(
                    {"kind": "error", "text": f"응답을 받지 못했습니다: {exc}"},
                    ensure_ascii=False,
                )
                + "\n\n"
            )

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/status", summary="챗봇 사용 가능 여부")
async def status(upstage: Upstage, _: CurrentPrincipal) -> dict:
    """키가 없으면 화면이 입력창을 열어 두지 않도록 미리 알려 준다."""
    return {"configured": upstage.configured, "model": upstage.model}
