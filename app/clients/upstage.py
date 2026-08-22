"""Upstage Solar 챗 완성 클라이언트.

Upstage 는 OpenAI 호환 인터페이스를 제공한다. base URL 과 모델명을 설정으로 빼 둔 것은
스펙이 바뀌어도 코드를 고치지 않기 위해서다 — 하드코딩하면 엔드포인트 경로 하나 바뀌었을 때
배포가 필요하다.

**키가 없으면 답을 지어내지 않는다.** 챗봇이 조용히 빈 답을 주면 화면은 "위험 없음"으로
읽고, 그것이 이 프로젝트가 금지하는 실패 방식이다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


class UpstageNotConfigured(RuntimeError):
    """API 키가 없다. 조용히 넘어가지 않고 화면까지 올린다."""


class UpstageClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None

    @property
    def configured(self) -> bool:
        return bool(self._settings.upstage_api_key)

    @property
    def model(self) -> str:
        return self._settings.upstage_model

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._settings.upstage_base_url.rstrip("/"),
                headers={
                    "Authorization": f"Bearer {self._settings.upstage_api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self._settings.upstage_timeout_s),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        # 추론 강도는 응답 시간과 직결된다. 대피 안내는 기다려 주지 않으므로 기본을 낮게 둔다.
        if self._settings.upstage_reasoning_effort:
            payload["reasoning_effort"] = self._settings.upstage_reasoning_effort
        return payload

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """한 번의 완성. 도구 호출 루프를 도는 쪽에서 반복 호출한다."""
        if not self.configured:
            raise UpstageNotConfigured(
                "SALGIL_UPSTAGE_API_KEY 가 없습니다 — 챗봇을 쓰려면 키를 설정하세요"
            )
        response = await self._http().post(
            "/chat/completions", json=self._payload(messages, tools, stream=False)
        )
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise RuntimeError("Upstage 응답에 choices 가 없습니다")
        return choices[0].get("message") or {}

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """토큰 델타를 그대로 흘린다. 화면이 첫 글자를 빨리 받게 하려는 것이다."""
        if not self.configured:
            raise UpstageNotConfigured(
                "SALGIL_UPSTAGE_API_KEY 가 없습니다 — 챗봇을 쓰려면 키를 설정하세요"
            )
        async with self._http().stream(
            "POST", "/chat/completions", json=self._payload(messages, tools, stream=True)
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    # 한 조각이 깨져도 스트림을 끊지 않는다. 다음 조각은 멀쩡할 수 있다.
                    logger.warning("upstage sent an unparseable chunk")
                    continue
                choices = chunk.get("choices") or []
                if choices:
                    yield choices[0]
