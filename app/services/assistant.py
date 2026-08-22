"""챗봇 대화 루프 — Upstage Solar 가 GB SafeData 도구를 쓴다.

루프가 백엔드에 있는 이유는 두 가지다. 정부 인증키가 상류에 있어 브라우저가 도구를 직접
부르면 안 되고, 도구 실패를 모델이 '위험 없음'으로 바꾸는 것을 막아야 한다.

**도구가 실패하면 실패했다고 모델에 말한다.** 빈 결과를 주면 모델은 도움이 되려고
"산사태 위험 없습니다"라고 답한다. 조회하지 못한 것과 위험이 없는 것은 다르고, 그 차이가
사람을 산비탈로 돌려보낼 수 있다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.gbsafe import GbSafeClient
from app.clients.upstage import UpstageClient
from app.core.config import Settings
from app.services import drills

logger = logging.getLogger(__name__)

DRILL_TOOL_NAME = drills.tool_spec()["function"]["name"]


def _tool_specs(raw: Any) -> list[dict[str, Any]]:
    """상류의 도구 목록을 OpenAI function-calling 스키마로 정규화한다."""
    items = raw.get("tools") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    specs: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # 이미 {"type": "function", "function": {...}} 형태면 그대로 쓴다.
        if item.get("type") == "function" and isinstance(item.get("function"), dict):
            specs.append(item)
            continue
        name = item.get("name")
        if not name:
            continue
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": item.get("description", ""),
                    "parameters": item.get("parameters") or {"type": "object", "properties": {}},
                },
            }
        )
    return specs


def _prompt_text(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, dict):
        for key in ("system_prompt", "prompt", "content", "text"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


async def _run_tool(
    gbsafe: GbSafeClient,
    name: str,
    arguments: str,
    session: AsyncSession | None = None,
) -> str:
    """도구 하나를 실행하고 결과를 모델이 읽을 문자열로 만든다."""
    try:
        params = json.loads(arguments) if arguments else {}
    except json.JSONDecodeError:
        return json.dumps(
            {"error": "arguments_not_json", "detail": arguments[:200]},
            ensure_ascii=False,
        )
    if not isinstance(params, dict):
        params = {}

    # 유일한 쓰기 도구. 훈련만 가능하고, 실제 경보는 사람이 누른다.
    if name == DRILL_TOOL_NAME:
        if session is None:
            return json.dumps(
                {"error": "no_session", "detail": "훈련을 개시할 수 없습니다"},
                ensure_ascii=False,
            )
        try:
            return json.dumps(
                await drills.start_drill(
                    session,
                    hazard=str(params.get("hazard", "")),
                    region_code=str(params.get("region_code", "")),
                    region_name=str(params.get("region_name", "")),
                    lat=params.get("lat"),
                    lon=params.get("lon"),
                    note=params.get("note"),
                ),
                ensure_ascii=False,
            )
        except Exception as exc:
            logger.warning("drill failed: %s", exc)
            return json.dumps(
                {"error": "drill_failed", "detail": str(exc)[:300]}, ensure_ascii=False
            )

    try:
        result = await gbsafe.call_tool(name, {k: str(v) for k, v in params.items()})
    except Exception as exc:  # 상류 장애·권한·타임아웃 전부
        logger.warning("tool %s failed: %s", name, exc)
        # 빈 결과가 아니라 실패라고 말한다. 이 구분이 사라지면 모델이 안심시킨다.
        return json.dumps(
            {
                "error": "tool_failed",
                "tool": name,
                "detail": str(exc)[:300],
                "instruction": (
                    "이 조회는 실패했다. 값이 없다고 답하지 말고, 확인하지 못했다고 답할 것."
                ),
            },
            ensure_ascii=False,
        )
    return json.dumps(result, ensure_ascii=False)[:20000]


async def converse(
    upstage: UpstageClient,
    gbsafe: GbSafeClient,
    settings: Settings,
    history: list[dict[str, Any]],
    session: AsyncSession | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """대화 한 턴. `{"kind": ...}` 이벤트를 순서대로 낸다.

    도구를 부르는 왕복은 스트리밍하지 않는다 — 델타로 조립한 tool_calls 는 조각이 깨지면
    조용히 인자가 빠진 채 실행된다. 마지막 답변만 스트리밍한다.
    """
    prompt_raw, tools_raw = await gbsafe.agent_system_prompt(), await gbsafe.tools()
    system = _prompt_text(prompt_raw)
    tools = _tool_specs(tools_raw)
    # 훈련 개시는 우리 도구다. 상류 도구는 설계상 전부 조회 전용이다.
    if session is not None:
        tools.append(drills.tool_spec())

    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    else:
        # 프롬프트를 못 받았으면 도구를 붙이지 않는다. 안전 규칙 없이 도구만 있는 모델은
        # 실패한 조회를 '이상 없음'으로 답한다.
        logger.warning("system prompt unavailable; running without tools")
        tools = []
    messages.extend(history)

    for round_index in range(settings.upstage_max_tool_rounds):
        message = await upstage.complete(messages, tools or None)
        calls = message.get("tool_calls") or []
        if not calls:
            messages.append({"role": "assistant", "content": message.get("content", "")})
            break
        messages.append(message)
        for call in calls:
            function = call.get("function") or {}
            name = function.get("name", "")
            yield {"kind": "tool", "name": name, "round": round_index + 1}
            output = await _run_tool(gbsafe, name, function.get("arguments", ""), session)
            if name == DRILL_TOOL_NAME:
                yield {"kind": "drill", "result": json.loads(output)}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "name": name,
                    "content": output,
                }
            )
    else:
        # 왕복 한도를 다 썼다. 여기서 멈추지 않으면 모델이 같은 도구를 계속 부른다.
        yield {
            "kind": "notice",
            "text": "도구 조회 한도에 도달해 지금까지 확인한 내용으로 답합니다.",
        }

    # 마지막 답변만 흘린다.
    async for choice in upstage.stream(messages, None):
        delta = (choice.get("delta") or {}).get("content")
        if delta:
            yield {"kind": "delta", "text": delta}
    yield {"kind": "done"}
