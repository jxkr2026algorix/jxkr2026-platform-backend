"""트랜잭션 경계를 응답 **이전**으로 옮기는 라우트 클래스.

FastAPI 의 `yield` 의존성은 정리 코드가 **응답을 보낸 뒤에** 실행된다. 세션 의존성에서
커밋하면 다음이 벌어진다.

    POST /incidents      → 201 을 받았다
    POST /incidents/{id}/plans → 404  ← 앞의 커밋이 아직 끝나지 않았다

프론트엔드는 방금 만든 상황에 곧바로 계획을 붙인다. 그래서 커밋은 응답을 만들고 난 직후,
클라이언트에게 보내기 **전에** 끝나야 한다. 커밋이 실패하면 201 이 아니라 500 이 나가야 한다.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute


class TransactionalRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            response = await original(request)
            session = getattr(request.state, "db_session", None)
            if session is not None and session.in_transaction():
                await session.commit()
            return response

        return handler
