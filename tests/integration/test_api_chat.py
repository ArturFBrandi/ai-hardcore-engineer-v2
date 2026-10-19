"""HTTP API (in-process ASGI app, real datastores, fake LLM)."""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import OPERATOR_SCOPE, auth_headers

pytestmark = pytest.mark.docker


async def test_chat_happy_path_contract(api) -> None:  # type: ignore[no-untyped-def]
    r = await api.post(
        "/v1/chat",
        json={"message": "When will the money from yesterday's sales be deposited?", "user_id": "cliente1988"},
        headers=auth_headers(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    for key in (
        "request_id",
        "trace_id",
        "conversation_id",
        "turn",
        "status",
        "answer",
        "intent",
        "agents",
        "route_source",
        "confidence",
        "escalated",
        "escalation_reasons",
        "case_id",
        "sources",
    ):
        assert key in body
    assert body["intent"] == "customer_settlement"
    assert {"router", "customer_support", "knowledge", "supervisor", "synthesizer"} <= set(body["agents"])
    assert body["turn"] == 1
    assert r.headers["x-request-id"] == body["request_id"]
    assert r.headers["traceparent"].split("-")[1] == body["trace_id"]
    assert body["trace_id"] != "0" * 32


async def test_body_user_id_is_optional_and_must_match_sub(api) -> None:  # type: ignore[no-untyped-def]
    ok = await api.post(
        "/v1/chat", json={"message": "Qual a diferença entre a Get Clássica e a Get Smart?"}, headers=auth_headers()
    )
    assert ok.status_code == 200
    bad = await api.post(
        "/v1/chat",
        json={"message": "minhas vendas de ontem", "user_id": "cliente2077"},
        headers=auth_headers("cliente1988"),
    )
    assert bad.status_code == 403
    assert bad.json()["error"] == "forbidden"


async def test_missing_token_in_jwt_mode_is_401(api) -> None:  # type: ignore[no-untyped-def]
    r = await api.post("/v1/chat", json={"message": "oi", "user_id": "cliente1988"})
    assert r.status_code == 401
    assert set(r.json()) == {"error", "detail", "request_id"}


async def test_foreign_conversation_is_generic_404(api) -> None:  # type: ignore[no-untyped-def]
    r1 = await api.post(
        "/v1/chat", json={"message": "Quais foram minhas vendas de ontem?"}, headers=auth_headers("cliente1988")
    )
    conv = r1.json()["conversation_id"]
    foreign = await api.post(
        "/v1/chat", json={"message": "e anteontem?", "conversation_id": conv}, headers=auth_headers("cliente2077")
    )
    missing = await api.post(
        "/v1/chat",
        json={"message": "e anteontem?", "conversation_id": "conv_" + uuid.uuid4().hex[:24]},
        headers=auth_headers("cliente2077"),
    )
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["detail"] == missing.json()["detail"]
    g = await api.get(f"/v1/conversations/{conv}", headers=auth_headers("cliente2077"))
    assert g.status_code == 404


async def test_follow_up_turn_keeps_context(api) -> None:  # type: ignore[no-untyped-def]
    r1 = await api.post("/v1/chat", json={"message": "Quais foram minhas vendas de ontem?"}, headers=auth_headers())
    conv = r1.json()["conversation_id"]
    r2 = await api.post(
        "/v1/chat", json={"message": "E a de anteontem?", "conversation_id": conv}, headers=auth_headers()
    )
    assert r2.status_code == 200, r2.text
    b2 = r2.json()
    assert b2["turn"] == 2
    assert b2["intent"] == "customer_transaction"
    assert b2["language"] == "pt"
    detail = await api.get(f"/v1/conversations/{conv}", headers=auth_headers())
    roles = [t["role"] for t in detail.json()["turns"]]
    assert roles == ["customer", "assistant", "customer", "assistant"]


async def test_validation_errors_use_envelope(api) -> None:  # type: ignore[no-untyped-def]
    r = await api.post("/v1/chat", json={"message": "", "user_id": "cliente1988"}, headers=auth_headers())
    assert r.status_code == 422
    assert r.json()["error"] == "validation_error"
    r = await api.post("/v1/chat", json={"message": "x", "unexpected": 1}, headers=auth_headers())
    assert r.status_code == 422


async def test_operator_routes_reject_customer_tokens(api) -> None:  # type: ignore[no-untyped-def]
    r = await api.get("/v1/operator/cases", headers=auth_headers())
    assert r.status_code == 403
    r = await api.get("/v1/operator/cases", headers=auth_headers("operator.ana", OPERATOR_SCOPE))
    assert r.status_code == 200


async def test_debug_trace_available_in_dev(api) -> None:  # type: ignore[no-untyped-def]
    r = await api.post("/v1/chat", json={"message": "Minha maquininha não conecta na internet"}, headers=auth_headers())
    rid = r.json()["request_id"]
    t = await api.get(f"/v1/debug/traces/{rid}", headers=auth_headers("operator.ana", OPERATOR_SCOPE))
    assert t.status_code == 200
    doc = t.json()
    assert doc["request_id"] == rid
    agents = [s["agent"] for s in doc["steps"]]
    assert "supervisor" in agents
    assert doc["cost"]["detail"]["llm_calls"] >= 1
