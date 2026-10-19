"""Typed application settings.

Environment variables are the only configuration source. Critical misconfiguration fails fast at
startup (``ConfigurationError``). Secrets are ``SecretStr`` and ``safe_dict()`` is the only
sanctioned way to log settings.
"""

from __future__ import annotations

import re
from enum import StrEnum
from functools import lru_cache
from typing import Any

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.errors import ConfigurationError


class AppEnv(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class AuthMode(StrEnum):
    JWT = "jwt"
    DEMO = "demo"


class LLMProviderName(StrEnum):
    MINIMAX = "minimax"
    FAKE = "fake"


class WebSearchProviderName(StrEnum):
    TAVILY = "tavily"
    DISABLED = "disabled"


_DSN_PASSWORD = re.compile(r"(?P<scheme>[a-z+]+://[^:/@]+):(?P<pw>[^@]+)@")


def redact_dsn(dsn: str) -> str:
    """Hide the password part of a DSN (``postgresql://user:***@host``)."""
    return _DSN_PASSWORD.sub(lambda m: f"{m.group('scheme')}:***@", dsn)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    # --- application -----------------------------------------------------------------------
    app_env: AppEnv = AppEnv.DEVELOPMENT
    app_version: str = "1.0.0"
    git_sha: str = "unknown"
    log_level: str = "INFO"
    log_json: bool = True

    # --- identity --------------------------------------------------------------------------
    auth_mode: AuthMode = AuthMode.JWT
    jwt_issuer: str = "https://dev-idp.local"
    jwt_audience: str = "getnet-support-api"
    jwt_jwks_url: str = "file:///keys/jwks.json"
    jwt_algorithms: list[str] = Field(default_factory=lambda: ["RS256", "ES256"])
    jwt_leeway_seconds: int = Field(default=30, ge=0, le=300)
    jwt_dev_private_key_path: str = "/keys/dev-private.pem"
    jwt_dev_kid: str = "dev-key-1"
    jwks_cache_seconds: int = Field(default=300, ge=0)

    # --- LLM (MiniMax-M3 is the only reasoning model) ---------------------------------------
    llm_provider: LLMProviderName = LLMProviderName.MINIMAX
    minimax_api_key: SecretStr = SecretStr("")
    minimax_base_url: str = "https://api.minimax.io/v1"
    minimax_model: str = "MiniMax-M3"
    llm_timeout_seconds: float = Field(default=45.0, gt=0, le=300)
    llm_max_retries: int = Field(default=2, ge=0, le=6)
    llm_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    llm_price_input_per_mtok: float = Field(default=0.30, ge=0)
    llm_price_output_per_mtok: float = Field(default=1.20, ge=0)
    max_tokens_per_request: int = Field(default=40000, ge=1000)
    llm_breaker_failure_threshold: int = Field(default=5, ge=1)
    llm_breaker_reset_seconds: float = Field(default=30.0, gt=0)

    # --- datastores ------------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://getnet:getnet@localhost:5432/getnet"
    db_pool_size: int = Field(default=10, ge=1)
    db_statement_timeout_ms: int = Field(default=10000, ge=100)
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "getnet_kb"
    qdrant_timeout_seconds: float = Field(default=10.0, gt=0)
    redis_url: str = "redis://localhost:6379/0"
    redis_timeout_seconds: float = Field(default=2.0, gt=0)

    # --- web search ------------------------------------------------------------------------
    web_search_provider: WebSearchProviderName = WebSearchProviderName.TAVILY
    web_search_api_key: SecretStr = SecretStr("")
    web_search_timeout_seconds: float = Field(default=15.0, gt=0)
    web_search_max_results: int = Field(default=5, ge=1, le=10)

    # --- RAG -------------------------------------------------------------------------------
    models_dir: str = "/models"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    sparse_model: str = "Qdrant/bm25"
    reranker_model: str = "jinaai/jina-reranker-v2-base-multilingual"
    rag_top_k: int = Field(default=15, ge=1, le=100)
    rag_rerank_top_k: int = Field(default=8, ge=1, le=50)
    rag_context_top_k: int = Field(default=5, ge=1, le=20)
    rag_min_score: float = 0.0
    rag_rerank_min_score: float = -2.0  # overwritten by `make calibrate` results (see .env.example)
    rag_product_boost: float = Field(default=0.0, ge=0.0)
    ingest_user_agent: str = "GetnetSupportAgentBot/1.0 (+evaluation crawler; respects robots.txt)"
    ingest_rate_limit_seconds: float = Field(default=1.0, ge=0.0)
    ingest_timeout_seconds: float = Field(default=20.0, gt=0)

    # --- decision / supervisor -------------------------------------------------------------
    escalation_confidence_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    escalation_penalties_file: str = "config/escalation_penalties.yaml"
    troubleshooting_max_attempts: int = Field(default=3, ge=1, le=10)
    supervisor_max_steps: int = Field(default=3, ge=1, le=8)
    supervisor_llm_enabled: bool = True
    customer_support_max_tool_rounds: int = Field(default=4, ge=1, le=8)
    graph_recursion_limit: int = Field(default=40, ge=10, le=200)

    # --- memory ----------------------------------------------------------------------------
    memory_window_turns: int = Field(default=8, ge=1, le=50)
    memory_token_budget: int = Field(default=1800, ge=200)
    conversation_ttl_days: int = Field(default=30, ge=1)
    conversation_lock_timeout_seconds: float = Field(default=60.0, gt=0)

    # --- HITL ------------------------------------------------------------------------------
    hitl_sla_minutes: int = Field(default=30, ge=1)
    operator_webhook_url: str = ""
    outbox_max_attempts: int = Field(default=5, ge=1)
    worker_poll_seconds: float = Field(default=2.0, gt=0)

    # --- API protection --------------------------------------------------------------------
    rate_limit_per_minute: int = Field(default=30, ge=1)
    rate_limit_ip_per_minute: int = Field(default=120, ge=1)
    rate_limit_operator_writes_per_minute: int = Field(default=20, ge=1)
    rate_limit_fail_open: bool = False
    max_request_body_bytes: int = Field(default=16384, ge=1024)
    request_timeout_seconds: float = Field(default=120.0, gt=0)
    idempotency_ttl_seconds: int = Field(default=86400, ge=60)
    enable_debug_endpoints: bool = True
    cors_allow_origins: list[str] = Field(default_factory=list)

    # --- observability ---------------------------------------------------------------------
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://otel-collector:4318"
    otel_service_name: str = "getnet-support-api"
    trace_store_ttl_seconds: int = Field(default=86400, ge=60)

    @field_validator("cors_allow_origins", "jwt_algorithms", mode="before")
    @classmethod
    def _split_csv(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("log_level")
    @classmethod
    def _valid_log_level(cls, value: str) -> str:
        upper = value.upper()
        if upper not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError(f"invalid LOG_LEVEL {value!r}")
        return upper

    @model_validator(mode="after")
    def _production_rules(self) -> Settings:
        if self.app_env is AppEnv.PRODUCTION:
            problems: list[str] = []
            if self.auth_mode is AuthMode.DEMO:
                problems.append("AUTH_MODE=demo is refused when APP_ENV=production")
            if self.llm_provider is LLMProviderName.FAKE:
                problems.append("LLM_PROVIDER=fake is refused when APP_ENV=production")
            if not self.minimax_api_key.get_secret_value():
                problems.append("MINIMAX_API_KEY is required in production")
            if self.jwt_jwks_url.startswith("file://"):
                problems.append("JWT_JWKS_URL must be an https OIDC JWKS endpoint in production")
            if "*" in self.cors_allow_origins:
                problems.append("CORS wildcard is refused in production")
            if problems:
                raise ConfigurationError("; ".join(problems))
            # Debug endpoints are force-disabled in production regardless of configuration.
            self.enable_debug_endpoints = False
        if self.rag_rerank_top_k > self.rag_top_k:
            raise ConfigurationError("RAG_RERANK_TOP_K must be <= RAG_TOP_K")
        if self.rag_context_top_k > self.rag_rerank_top_k:
            raise ConfigurationError("RAG_CONTEXT_TOP_K must be <= RAG_RERANK_TOP_K")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env is AppEnv.PRODUCTION

    @property
    def psycopg_dsn(self) -> str:
        """DSN for psycopg v3 (used by the LangGraph checkpointer)."""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    def safe_dict(self) -> dict[str, Any]:
        """Settings with secrets and DSN passwords redacted — the only way settings are logged."""
        data = self.model_dump()
        for key, value in list(data.items()):
            if isinstance(getattr(self, key), SecretStr):
                data[key] = "***set***" if getattr(self, key).get_secret_value() else "***unset***"
            elif isinstance(value, str) and "://" in value:
                data[key] = redact_dsn(value)
        return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    try:
        return Settings()
    except ConfigurationError:
        raise
    except ValueError as exc:  # pydantic ValidationError is a ValueError subclass
        raise ConfigurationError(f"invalid configuration: {exc}") from exc
