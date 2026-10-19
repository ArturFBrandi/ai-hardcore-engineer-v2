"""Request context (pure ASGI): request id, OTel-correlated logging context, X-Request-ID / traceparent
headers, security headers, body-size limit, pre-auth IP rate limit, HTTP metrics."""

from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import AppError, RateLimitedError
from app.observability import metrics
from app.observability.tracing import current_ids, traceparent

_RID = re.compile(r"^[A-Za-z0-9_.-]{8,64}$")
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cache-control", b"no-store"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (
        b"content-security-policy",
        b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
    ),
]


def _error_body(status: int, code: str, detail: str, request_id: str) -> bytes:
    return json.dumps({"error": code, "detail": detail, "request_id": request_id}).encode()


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        self.app = app
        self.max_body = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        incoming = headers.get("x-request-id", "")
        request_id = incoming if _RID.match(incoming) else f"req_{uuid.uuid4().hex[:20]}"
        scope.setdefault("state", {})["request_id"] = request_id
        path: str = scope.get("path", "")
        started = time.perf_counter()
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id, path=path, method=scope.get("method"))
        status_holder = {"code": 500}

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
                hdrs: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                hdrs.append((b"x-request-id", request_id.encode()))
                hdrs.append((b"traceparent", traceparent().encode()))
                hdrs.extend(
                    SECURITY_HEADERS if not path.startswith(("/ui", "/operator", "/docs")) else SECURITY_HEADERS[:5]
                )
                message["headers"] = hdrs
            await send(message)

        async def reject(status: int, code: str, detail: str, extra: list[tuple[bytes, bytes]] | None = None) -> None:
            await send_with_headers(
                {
                    "type": "http.response.start",
                    "status": status,
                    "headers": [(b"content-type", b"application/json"), *(extra or [])],
                }
            )
            await send_with_headers(
                {"type": "http.response.body", "body": _error_body(status, code, detail, request_id)}
            )

        try:
            declared = int(headers.get("content-length", "0") or 0)
        except ValueError:
            declared = 0
        if declared > self.max_body:
            await reject(413, "payload_too_large", f"request body exceeds {self.max_body} bytes")
            return
        if path.startswith("/v1/"):
            client = scope.get("client") or ("unknown", 0)
            ip = headers.get("x-forwarded-for", "").split(",")[0].strip() or client[0]
            try:
                await scope["app"].state.rate_limiter.check_ip(ip)
            except RateLimitedError as exc:
                await reject(429, exc.code, exc.detail, [(b"retry-after", str(exc.retry_after).encode())])
                return
            except AppError as exc:
                await reject(exc.status_code, exc.code, exc.public_detail)
                return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body:
                    raise _BodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send_with_headers)
        except _BodyTooLarge:
            await reject(413, "payload_too_large", f"request body exceeds {self.max_body} bytes")
        finally:
            route = _route_template(scope)
            metrics.HTTP_REQUESTS.labels(
                method=scope.get("method", ""), route=route, code=str(status_holder["code"])
            ).inc()
            trace_id, _ = current_ids()
            structlog.get_logger("access").info(
                "http_request",
                status=status_holder["code"],
                route=route,
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
                otel_trace_id=trace_id,
            )


class _BodyTooLarge(Exception):
    pass


def _route_template(scope: Scope) -> str:
    route: Any = scope.get("route")
    return getattr(route, "path", None) or "unmatched"
