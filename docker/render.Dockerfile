# Optional JS-rendering crawler image (render profile). Kept separate so the API image stays small:
# Playwright + Chromium add ~1.5 GB that only the crawler needs.
FROM mcr.microsoft.com/playwright/python:v1.64.0-noble@sha256:684df257a1fa0ef3fa288239819984008f560e8f8db5b635ee9e2a50cbcd2e23
COPY --from=ghcr.io/astral-sh/uv:0.12.21@sha256:a7aed3216253ee804de3e2d8afa5073baa1a177335345d43845cd4165e43b711 /uv /bin/
ENV UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv UV_PYTHON_DOWNLOADS=never MODELS_DIR=/models HF_HOME=/models/hf
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project --python /usr/bin/python3 && uv pip install --python /app/.venv/bin/python playwright==1.64.0
COPY app ./app
COPY ingestion ./ingestion
COPY config ./config
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv sync --frozen --no-dev --python /usr/bin/python3 && uv pip install --python /app/.venv/bin/python playwright==1.64.0
ENV PATH=/app/.venv/bin:$PATH PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
USER pwuser
CMD ["python", "-m", "ingestion.pipeline", "crawl"]
