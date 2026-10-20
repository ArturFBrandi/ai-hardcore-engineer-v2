# syntax=docker/dockerfile:1.7
# Multi-stage image: builder (locked deps) → test (dev deps + tests) → runtime (non-root, no ML models baked in).
# ML models are downloaded once into the `models` volume by the one-shot `models-init` service.

ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.21@sha256:a7aed3216253ee804de3e2d8afa5073baa1a177335345d43845cd4165e43b711

FROM ${UV_IMAGE} AS uv

# ---------------------------------------------------------------------------------- builder
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY ingestion ./ingestion
COPY evaluation ./evaluation
COPY scripts ./scripts
COPY alembic ./alembic
COPY alembic.ini ./
COPY config ./config
COPY data/snapshots ./data/snapshots
COPY docker/entrypoint.sh ./docker/entrypoint.sh
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

# ------------------------------------------------------------------------------------- test
FROM builder AS test
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen
COPY tests ./tests
COPY Makefile README.md ./
ENV PATH=/app/.venv/bin:$PATH APP_ENV=test
CMD ["pytest", "-q"]

# ---------------------------------------------------------------------------------- runtime
FROM ${PYTHON_IMAGE} AS runtime
ARG GIT_SHA=unknown
ARG APP_VERSION=1.0.0
LABEL org.opencontainers.image.title="getnet-support-agents" \
      org.opencontainers.image.version="${APP_VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.source="https://github.com/ArturFBrandi/ai-hardcore-engineer-v2"
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app \
    && mkdir -p /models /keys && chown app:app /models
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MODELS_DIR=/models \
    HF_HOME=/models/hf GIT_SHA=${GIT_SHA} APP_VERSION=${APP_VERSION}
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=6 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["api"]
