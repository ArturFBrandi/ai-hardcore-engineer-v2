"""Translate every error into the uniform ``ErrorResponse`` envelope (no stack traces to clients)."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.schemas import ErrorResponse
from app.errors import AppError, RateLimitedError
from app.observability.logging import get_logger

log = get_logger(__name__)

_HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
    503: "dependency_unavailable",
}


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def error_response(
    request: Request, status: int, error: str, detail: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    body = ErrorResponse(error=error, detail=detail, request_id=_request_id(request))
    return JSONResponse(status_code=status, content=body.model_dump(), headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        headers: dict[str, str] = {}
        if isinstance(exc, RateLimitedError):
            headers["Retry-After"] = str(exc.retry_after)
        if exc.status_code >= 500:
            log.error("app_error", code=exc.code, detail=exc.detail)
            detail = exc.public_detail
        else:
            detail = exc.detail
        return error_response(request, exc.status_code, exc.code, detail, headers or None)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [
            f"{'.'.join(str(p) for p in err.get('loc', ()) if p != 'body')}: {err.get('msg', 'invalid')}"
            for err in exc.errors()
        ]
        return error_response(request, 422, "validation_error", "; ".join(problems)[:500])

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        detail = exc.detail if isinstance(exc.detail, str) else code
        return error_response(request, exc.status_code, code, detail, dict(exc.headers) if exc.headers else None)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", error_type=type(exc).__name__)
        return error_response(request, 500, "internal_error", "An internal error occurred.")
