"""Provider factory — the only place the concrete LLM provider is chosen."""

from __future__ import annotations

from app.config import LLMProviderName, Settings
from app.errors import ConfigurationError
from app.llm.base import LLMProvider
from app.llm.fake import FakeLLMProvider
from app.llm.minimax import MiniMaxProvider


def build_llm(settings: Settings) -> LLMProvider:
    if settings.llm_provider is LLMProviderName.FAKE:
        if settings.is_production:
            raise ConfigurationError("LLM_PROVIDER=fake is refused when APP_ENV=production")
        return FakeLLMProvider()
    return MiniMaxProvider(settings)
