"""오류 의미론.

가장 중요한 규칙 하나: **조회 실패를 빈 결과로 바꾸지 않는다.**

GB SafeData 가 실제로 겪은 가장 위험한 결함이 그것이었다 —
`{"error":"denied"}` 가 빈 목록으로 파싱되어 "통제된 도로 없음"으로 읽혔다.
상류가 실패하면 여기서도 실패로 전파하거나, 최소한 `degradations` 로 남긴다.
`except: return []` 는 이 저장소에서 금지다.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

log = get_logger(__name__)

PROBLEM_JSON = "application/problem+json"


class SalgilError(Exception):
    """이 서비스가 던지는 모든 오류의 뿌리."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    problem_type: str = "about:blank"
    title: str = "내부 오류"

    def __init__(self, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.extra = extra

    def to_problem(self, instance: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "type": self.problem_type,
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
            "instance": instance,
        }
        body.update(self.extra)
        return body


class UpstreamError(SalgilError):
    """상류(GB SafeData, ML Engine)가 응답하지 못했다.

    이 예외는 절대 빈 데이터로 축소되지 않는다. 화면에는 UNVERIFIED 로 그려져야 한다.
    """

    status_code = status.HTTP_502_BAD_GATEWAY
    problem_type = "https://salgil.gyeongbuk.kr/problems/upstream-unavailable"
    title = "상류 데이터 원천을 읽지 못했습니다"

    def __init__(
        self,
        detail: str,
        *,
        upstream: str,
        upstream_status: int | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(detail, upstream=upstream, upstream_status=upstream_status, **extra)
        self.upstream = upstream
        self.upstream_status = upstream_status


class UpstreamTimeout(UpstreamError):
    status_code = status.HTTP_504_GATEWAY_TIMEOUT
    problem_type = "https://salgil.gyeongbuk.kr/problems/upstream-timeout"
    title = "상류 응답이 시간 안에 오지 않았습니다"


class NotFoundError(SalgilError):
    status_code = status.HTTP_404_NOT_FOUND
    problem_type = "https://salgil.gyeongbuk.kr/problems/not-found"
    title = "대상을 찾지 못했습니다"


class ConflictError(SalgilError):
    """운영 절차상 지금 할 수 없는 일 (예: 승인 전 주민연락 개시)."""

    status_code = status.HTTP_409_CONFLICT
    problem_type = "https://salgil.gyeongbuk.kr/problems/workflow-conflict"
    title = "지금 수행할 수 없는 절차입니다"


class ValidationError(SalgilError):
    status_code = 422  # Starlette 버전마다 상수 이름이 달라 리터럴을 쓴다
    problem_type = "https://salgil.gyeongbuk.kr/problems/invalid-request"
    title = "요청이 올바르지 않습니다"


class AuthError(SalgilError):
    status_code = status.HTTP_401_UNAUTHORIZED
    problem_type = "https://salgil.gyeongbuk.kr/problems/unauthenticated"
    title = "인증이 필요합니다"


class ForbiddenError(SalgilError):
    status_code = status.HTTP_403_FORBIDDEN
    problem_type = "https://salgil.gyeongbuk.kr/problems/forbidden"
    title = "권한이 없습니다"


def install_exception_handlers(app: Any) -> None:
    @app.exception_handler(SalgilError)
    async def _salgil(request: Request, exc: SalgilError) -> JSONResponse:
        if isinstance(exc, UpstreamError):
            log.warning(
                "upstream_failed",
                upstream=exc.upstream,
                upstream_status=exc.upstream_status,
                detail=exc.detail,
                path=request.url.path,
            )
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, AuthError) else None
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_problem(str(request.url.path)),
            media_type=PROBLEM_JSON,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "type": ValidationError.problem_type,
                "title": ValidationError.title,
                "status": 422,
                "detail": "요청 본문 또는 질의 인자를 확인하세요",
                "instance": str(request.url.path),
                "errors": jsonable_encoder(exc.errors()),
            },
            media_type=PROBLEM_JSON,
        )

    @app.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "type": "about:blank",
                "title": exc.detail if isinstance(exc.detail, str) else "요청 실패",
                "status": exc.status_code,
                "detail": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
                "instance": str(request.url.path),
            },
            media_type=PROBLEM_JSON,
            headers=getattr(exc, "headers", None),
        )
