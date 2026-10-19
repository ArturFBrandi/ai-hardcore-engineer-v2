"""Shared fixtures.

Datastore-backed tests use dedicated test resources (database ``getnet_test``, Redis DB 15, Qdrant
collection ``getnet_test``) so they never disturb the demo data. Start the datastores with
``docker compose up -d postgres qdrant redis`` (``make test`` does it for you).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator

import asyncpg
import pytest

from app.config import Settings
from app.db.base import Database
from app.tools.context import AuthContext

TEST_DB_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://getnet:getnet-dev-only@localhost:55432/getnet_test"
)
TEST_REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:56379/15")
TEST_QDRANT_URL = os.environ.get("TEST_QDRANT_URL", "http://localhost:56333")


def make_test_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "app_env": "test",
        "llm_provider": "fake",
        "auth_mode": "jwt",
        "database_url": TEST_DB_URL,
        "redis_url": TEST_REDIS_URL,
        "qdrant_url": TEST_QDRANT_URL,
        "qdrant_collection": "getnet_test",
        "web_search_provider": "disabled",
        "log_json": True,
        "log_level": "WARNING",
        "otel_enabled": False,
        "models_dir": os.environ.get("MODELS_DIR", os.path.expanduser("~/.cache/getnet-models")),
        "jwt_jwks_url": "file://" + os.path.abspath(".dev-keys/test/jwks.json"),
        "jwt_dev_private_key_path": os.path.abspath(".dev-keys/test/dev-private.pem"),
        "rate_limit_per_minute": 1000,
        "rate_limit_ip_per_minute": 5000,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture(scope="session")
def settings() -> Settings:
    return make_test_settings()


@pytest.fixture(scope="session")
async def db(settings: Settings) -> AsyncIterator[Database]:
    from evaluation.environment import ensure_database, migrate, seed, setup_checkpointer

    try:
        await ensure_database(settings)
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover - environment problem
        pytest.exit(f"PostgreSQL not reachable ({exc}). Start datastores: make datastores", returncode=2)
    await asyncio.to_thread(migrate, settings)
    await setup_checkpointer(settings)
    await seed(settings)
    database = Database(settings)
    yield database
    await database.dispose()


@pytest.fixture
def ctx_1988() -> AuthContext:
    return AuthContext(customer_id="cliente1988", subject="cliente1988", scopes=frozenset({"chat"}), request_id="req_t")


@pytest.fixture
def ctx_2077() -> AuthContext:
    return AuthContext(customer_id="cliente2077", subject="cliente2077", scopes=frozenset({"chat"}), request_id="req_t")


@pytest.fixture(scope="session")
def anyio_backend() -> Iterator[str]:
    return "asyncio"


@pytest.fixture(scope="session")
async def container(settings: Settings, db: Database) -> AsyncIterator[object]:
    from app.container import build_container
    from app.llm.fake import FakeLLMProvider
    from evaluation.environment import index_snapshot
    from tests.fakes import FakeWebSearch

    await index_snapshot(settings)
    c = await build_container(settings, llm=FakeLLMProvider(), web=FakeWebSearch())
    await c.checkpointer.setup()
    yield c
    await c.close()


@pytest.fixture
def fake_llm(container):  # type: ignore[no-untyped-def]
    container.llm.reset()
    return container.llm


@pytest.fixture
def fake_web(container):  # type: ignore[no-untyped-def]
    container.web.down = False
    return container.web


@pytest.fixture(scope="session")
def dev_keys() -> str:
    from app.api.dev_idp import generate_keys

    generate_keys(".dev-keys/test")
    return ".dev-keys/test"


@pytest.fixture(scope="session")
async def api(container, settings: Settings, dev_keys: str) -> AsyncIterator[object]:  # type: ignore[no-untyped-def]
    """ASGI client running the real app (lifespan included) against the test container."""
    import httpx

    from app.main import create_app

    app = create_app(settings, container=container)
    async with app.router.lifespan_context(app):
        for _ in range(600):
            if app.state.models_warm:
                break
            await asyncio.sleep(0.1)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=120) as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


def token_for(sub: str, scope: str = "chat conversations:read conversations:delete", **kw: object) -> str:
    from app.api.dev_idp import mint

    return mint(sub, scope, settings=make_test_settings(), private_key_path=".dev-keys/test/dev-private.pem", **kw)  # type: ignore[arg-type]


def auth_headers(
    sub: str = "cliente1988", scope: str = "chat conversations:read conversations:delete"
) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_for(sub, scope)}"}


OPERATOR_SCOPE = "cases:read cases:write approvals:write"
