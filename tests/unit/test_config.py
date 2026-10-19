import pytest

from app.config import AppEnv, AuthMode, LLMProviderName, Settings, redact_dsn
from app.errors import ConfigurationError


def test_defaults_are_valid() -> None:
    s = Settings()
    assert s.minimax_model == "MiniMax-M3"
    assert s.minimax_base_url == "https://api.minimax.io/v1"


def test_safe_dict_redacts_secrets_and_dsn_passwords() -> None:
    s = Settings(
        minimax_api_key="super-secret-value",
        web_search_api_key="tvly-abc",
        database_url="postgresql+asyncpg://user:hunter2@db:5432/x",
    )
    d = s.safe_dict()
    flat = repr(d)
    assert "super-secret-value" not in flat
    assert "hunter2" not in flat
    assert d["minimax_api_key"] == "***set***"
    assert "***" in d["database_url"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"auth_mode": AuthMode.DEMO}, "AUTH_MODE=demo"),
        ({"llm_provider": LLMProviderName.FAKE}, "LLM_PROVIDER=fake"),
        ({"minimax_api_key": ""}, "MINIMAX_API_KEY"),
    ],
)
def test_production_refuses_unsafe_modes(overrides: dict[str, object], message: str) -> None:
    base: dict[str, object] = {
        "app_env": AppEnv.PRODUCTION,
        "minimax_api_key": "k",
        "jwt_jwks_url": "https://idp.example.com/.well-known/jwks.json",
    }
    base.update(overrides)
    with pytest.raises(ConfigurationError, match=message):
        Settings(**base)  # type: ignore[arg-type]


def test_production_force_disables_debug_endpoints() -> None:
    s = Settings(
        app_env=AppEnv.PRODUCTION,
        minimax_api_key="k",
        jwt_jwks_url="https://idp.example.com/jwks",
        enable_debug_endpoints=True,
    )
    assert s.enable_debug_endpoints is False


def test_rag_k_ordering_validated() -> None:
    with pytest.raises(ConfigurationError):
        Settings(rag_top_k=5, rag_rerank_top_k=8)


def test_csv_lists_parse() -> None:
    s = Settings(cors_allow_origins="http://a.com, http://b.com")  # type: ignore[arg-type]
    assert s.cors_allow_origins == ["http://a.com", "http://b.com"]


def test_redact_dsn() -> None:
    assert redact_dsn("redis://default:pw@redis:6379/0") == "redis://default:***@redis:6379/0"
