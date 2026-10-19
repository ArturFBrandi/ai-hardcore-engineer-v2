"""OpenTelemetry setup.

The SDK tracer provider is always installed so every request has a real trace/span id (one correlated
id space for logs, debug traces, ``traceparent`` and the OTLP backend). Spans are exported over OTLP/HTTP
only when ``OTEL_ENABLED=true`` (observability Compose profile → OTel Collector → Jaeger).
"""

from __future__ import annotations

from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import Settings

_configured = False


def setup_tracing(settings: Settings, service_name: str | None = None) -> None:
    global _configured  # noqa: PLW0603 - process-wide, idempotent provider installation
    if _configured:
        return
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": service_name or settings.otel_service_name,
                "service.version": settings.app_version,
                "deployment.environment": settings.app_env.value,
            }
        )
    )
    if settings.otel_enabled:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # noqa: PLC0415 - optional

        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/traces")
            )
        )
    trace.set_tracer_provider(provider)
    _configured = True


def current_ids() -> tuple[str, str]:
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return "0" * 32, "0" * 16
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")


def traceparent() -> str:
    trace_id, span_id = current_ids()
    return f"00-{trace_id}-{span_id}-01"


def instrument_app(app: Any) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: PLC0415 - imported only by the API

    FastAPIInstrumentor.instrument_app(app, excluded_urls="health/live,health/ready,metrics")
