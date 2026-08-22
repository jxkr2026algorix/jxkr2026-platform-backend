"""챗봇 연동 프록시.

GB SafeData 는 도구 정의와 시스템 프롬프트를 HTTP 로 준다. 이 백엔드가 중계하는 이유는
정부 인증키가 상류에 있어서 브라우저가 직접 부르면 안 되기 때문이다.

**시스템 프롬프트를 빼고 도구만 붙이면 사고가 난다.** 모델은 기본적으로 도움이 되려 하고,
산사태 조회가 403 으로 실패해 결과가 비면 "산사태 위험 없습니다"라고 답한다.
그래서 프롬프트를 복붙하지 않고 매번 상류에서 받아 쓴다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.api.deps import CurrentPrincipal, GbSafe
from app.api.route import TransactionalRoute

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
