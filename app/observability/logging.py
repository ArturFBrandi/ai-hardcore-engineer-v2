"""Structured JSON logging (structlog) correlated with OpenTelemetry trace/span ids.

Every log line carries ``request_id`` (bound per request through contextvars) and the current OTel
``trace_id``/``span_id`` so logs, traces and the debug trace store share one id space. A final
processor redacts secrets and card/document numbers so nothing sensitive reaches stdout.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from opentelemetry import trace

from app.guardrails.redaction import redact_text

_SENSITIVE_KEYS = {"authorization", "api_key", "password", "token", "secret", "minimax_api_key", "web_search_api_key"}


def _add_otel_ids(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx.is_valid:
        event_dict.setdefault("trace_id", format(ctx.trace_id, "032x"))
        event_dict.setdefault("span_id", format(ctx.span_id, "016x"))
    return event_dict


def _redact(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key, value in list(event_dict.items()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "***"
        elif isinstance(value, str):
            event_dict[key] = redact_text(value)
    return event_dict


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_otel_ids,
        structlog.processors.format_exc_info,
        _redact,
    ]
    renderer: Any = structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )
    # Route stdlib logging (uvicorn, alembic, httpx) through a plain handler at the same level.
    logging.basicConfig(level=level, stream=sys.stdout, format="%(message)s", force=True)
    for noisy in ("httpx", "httpcore", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(max(logging.getLevelName(level), logging.WARNING))


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
