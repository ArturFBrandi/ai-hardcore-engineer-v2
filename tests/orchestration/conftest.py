from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import pytest

from app.tools.context import AuthContext


def ctx(customer: str = "cliente1988") -> AuthContext:
    return AuthContext(customer_id=customer, subject=customer, scopes=frozenset({"chat"}))


def events(state: dict[str, Any], agent: str, name: str | None = None) -> list[dict[str, Any]]:
    return [e for e in state.get("events", []) if e["agent"] == agent and (name is None or e["event"] == name)]


def interval(e: dict[str, Any]) -> tuple[datetime, datetime]:
    return datetime.fromisoformat(e["started_at"]), datetime.fromisoformat(e["ended_at"])


@pytest.fixture
def turn(container, fake_llm, fake_web):  # type: ignore[no-untyped-def]
    async def run(message: str, conversation_id: str | None = None, customer: str = "cliente1988") -> Any:
        return await container.chat.run_turn(
            ctx(customer), message, conversation_id, f"req_{uuid.uuid4().hex[:12]}", uuid.uuid4().hex
        )

    return run
