"""Vague-message routing rule."""

from __future__ import annotations

import pytest

from app.agents.knowledge import rewrite_query
from app.orchestration.routing import is_vague, rule_route


@pytest.mark.parametrize("msg", ["ajuda", "Oi!", "hello", "Preciso de ajuda", "can you help me?"])
def test_vague_messages_route_to_clarification_rule(msg: str) -> None:
    m = rule_route(msg)
    assert m is not None and m.rule == "too_vague" and not m.flags["wants_human"]


@pytest.mark.parametrize("msg", ["ajuda com a maquininha que não conecta", "help me with my settlement"])
def test_messages_with_content_are_not_vague(msg: str) -> None:
    assert not is_vague(msg)


def test_english_troubleshooting_query_gets_portuguese_topic_terms() -> None:
    state = {
        "intent": "technical_support",
        "slots": {"active_terminal_model": "Get Smart", "troubleshooting_attempts": {"connectivity": 2}},
    }
    q = rewrite_query("Get Smart no internet after reboot connectivity troubleshooting", state)  # type: ignore[arg-type]
    assert "conecta" in q and "Get Smart" in q
    pt = "maquininha continua sem conexão internet após reiniciar"
    assert rewrite_query(pt, state) == pt  # type: ignore[arg-type]


def test_broadened_troubleshooting_retry_uses_the_topic_query() -> None:
    state = {
        "intent": "technical_support",
        "slots": {"active_terminal_model": "Get Smart", "troubleshooting_attempts": {"connectivity": 2}},
    }
    q = rewrite_query("Get Smart não conecta APN erro E-COM reiniciou", state, broaden=True)  # type: ignore[arg-type]
    assert q.startswith("Get Smart maquininha não conecta") and "E-COM" not in q
