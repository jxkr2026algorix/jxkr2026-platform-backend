"""챗봇 대화 루프.

**도구 실패는 실패로 전달돼야 한다.** 빈 결과를 주면 모델은 도움이 되려고 "산사태 위험
없습니다"라고 답한다. 조회하지 못한 것과 위험이 없는 것은 다르고, 그 차이가 사람을
산비탈로 돌려보낼 수 있다.
"""

from __future__ import annotations

import json

import pytest

from app.clients.upstage import UpstageClient, UpstageNotConfigured
from app.core.config import get_settings
from app.services.assistant import _prompt_text, _run_tool, _tool_specs, converse


def test_tool_specs_accept_a_bare_list_of_tools():
    specs = _tool_specs(
        {"tools": [{"name": "gbsafe_landslide", "description": "d", "parameters": {}}]}
    )
    assert specs[0]["type"] == "function"
    # 접두어는 이름의 일부다. 벗기면 상류에서 '그런 도구 없음'이 난다.
    assert specs[0]["function"]["name"] == "gbsafe_landslide"


def test_tool_specs_pass_through_openai_shaped_entries():
    given = {"type": "function", "function": {"name": "a", "parameters": {}}}
    assert _tool_specs([given]) == [given]


def test_prompt_text_reads_the_shapes_upstream_uses():
    assert _prompt_text("plain") == "plain"
    assert _prompt_text({"system_prompt": "from key"}) == "from key"
    assert _prompt_text({"nothing": 1}) == ""


class _FailingTools:
    async def call_tool(self, name, params):
        raise RuntimeError("403 Forbidden")


async def test_a_failed_tool_tells_the_model_it_failed():
    payload = json.loads(await _run_tool(_FailingTools(), "gbsafe_landslide", "{}"))
    assert payload["error"] == "tool_failed"
    # 값이 없다고 답하지 말라고 명시적으로 지시한다.
    assert "확인하지 못했다" in payload["instruction"]


async def test_unparseable_arguments_do_not_reach_upstream():
    payload = json.loads(await _run_tool(_FailingTools(), "t", "{not json"))
    assert payload["error"] == "arguments_not_json"


def test_client_without_a_key_reports_itself_unconfigured():
    settings = get_settings()
    assert UpstageClient(settings).configured is False


async def test_completing_without_a_key_raises_rather_than_returning_empty():
    client = UpstageClient(get_settings())
    with pytest.raises(UpstageNotConfigured):
        await client.complete([{"role": "user", "content": "hi"}])


class _StubUpstage:
    """도구를 한 번 부르고, 그다음 답을 흘린다."""

    configured = True
    model = "solar-pro-4"

    def __init__(self):
        self.calls = 0
        self.saw_tools = None

    async def complete(self, messages, tools=None):
        self.calls += 1
        self.saw_tools = tools
        if self.calls == 1:
            return {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "c1",
                        "function": {"name": "gbsafe_landslide", "arguments": "{}"},
                    }
                ],
            }
        return {"role": "assistant", "content": ""}

    async def stream(self, messages, tools=None):
        for piece in ["확인", "했습니다"]:
            yield {"delta": {"content": piece}}


class _StubTools:
    def __init__(self, prompt="규칙"):
        self._prompt = prompt

    async def agent_system_prompt(self):
        return {"system_prompt": self._prompt}

    async def tools(self):
        return {"tools": [{"name": "gbsafe_landslide", "parameters": {}}]}

    async def call_tool(self, name, params):
        return {"ok": True}


async def test_a_turn_runs_the_tool_then_streams_the_answer():
    events = [
        event
        async for event in converse(
            _StubUpstage(), _StubTools(), get_settings(), [{"role": "user", "content": "q"}]
        )
    ]
    kinds = [e["kind"] for e in events]
    assert kinds[0] == "tool"
    assert kinds[-1] == "done"
    assert "".join(e["text"] for e in events if e["kind"] == "delta") == "확인했습니다"


async def test_no_system_prompt_means_no_tools():
    """규칙 없이 도구만 붙은 모델은 실패한 조회를 '이상 없음'으로 답한다."""
    upstage = _StubUpstage()
    events = [
        event
        async for event in converse(
            upstage, _StubTools(prompt=""), get_settings(), [{"role": "user", "content": "q"}]
        )
    ]
    assert upstage.saw_tools is None
    assert events[-1]["kind"] == "done"


async def test_the_tool_loop_is_bounded(monkeypatch):
    """한도가 없으면 모델이 같은 도구를 끝없이 부른다."""

    class _AlwaysCalls(_StubUpstage):
        async def complete(self, messages, tools=None):
            return {
                "role": "assistant",
                "tool_calls": [
                    {"id": "x", "function": {"name": "gbsafe_landslide", "arguments": "{}"}}
                ],
            }

    settings = get_settings()
    monkeypatch.setattr(settings, "upstage_max_tool_rounds", 2, raising=False)
    events = [
        event
        async for event in converse(
            _AlwaysCalls(), _StubTools(), settings, [{"role": "user", "content": "q"}]
        )
    ]
    assert sum(1 for e in events if e["kind"] == "tool") == 2
    assert any(e["kind"] == "notice" for e in events)


async def test_chat_endpoint_reports_a_missing_key_instead_of_answering(client):
    response = await client.post(
        "/api/v1/assistant/chat", json={"messages": [{"role": "user", "content": "hi"}]}
    )
    assert response.status_code == 200
    assert "error" in response.text


async def test_status_says_whether_the_chatbot_can_run(client):
    body = (await client.get("/api/v1/assistant/status")).json()
    assert body["configured"] is False
    assert body["model"] == "solar-pro-4"
