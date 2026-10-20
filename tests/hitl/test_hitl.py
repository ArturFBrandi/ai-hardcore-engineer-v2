"""Human-in-the-loop: interrupt → operator → resume → customer → next turn; approvals; SLA; outbox; audit."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.db.models import ActionStatus, AuditLog, Outbox, OutboxStatus, PendingAction, ServiceOrder, SupportCase
from app.hitl.service import HitlService
from app.llm.base import LLMResponse
from app.llm.fake import tool_call_response
from app.worker.main import Sink, check_sla, deliver_outbox
from tests.conftest import OPERATOR_SCOPE, auth_headers

pytestmark = pytest.mark.docker
OP = "operator.ana"


async def _escalate(api, message: str = "Quero falar com um atendente humano", customer: str = "cliente1988") -> dict:  # type: ignore[no-untyped-def]
    r = await api.post("/v1/chat", json={"message": message}, headers=auth_headers(customer))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "awaiting_human", body
    assert body["case_id"]
    return body


async def _whole_queue(api, headers: dict[str, str]) -> list[dict]:  # type: ignore[no-untyped-def]
    items: list[dict] = []
    offset: int | None = 0
    while offset is not None:
        page = (await api.get(f"/v1/operator/cases?limit=100&offset={offset}", headers=headers)).json()
        items += page["items"]
        offset = page["next_offset"]
    return items


async def _audit_actions(container, case_id: str) -> list[str]:  # type: ignore[no-untyped-def]
    async with container.db.session() as s:
        rows = (await s.scalars(select(AuditLog).where(AuditLog.entity_id == case_id).order_by(AuditLog.id))).all()
    return [r.action for r in rows]


async def test_full_loop_reply_flows_back_and_next_turn_uses_it(api, container) -> None:  # type: ignore[no-untyped-def]
    body = await _escalate(api)
    case_id, conv = body["case_id"], body["conversation_id"]
    op = auth_headers(OP, OPERATOR_SCOPE)

    assert any(c["case_id"] == case_id and c["awaiting_human"] for c in await _whole_queue(api, op))
    detail = (await api.get(f"/v1/operator/cases/{case_id}", headers=op)).json()
    assert detail["context"]["intent"] == "human_support"
    assert detail["conversation_excerpt"]
    assert "CANARY" not in json.dumps(detail)

    claim = await api.post(f"/v1/operator/cases/{case_id}/claim", headers=op)
    assert claim.json()["status"] == "in_progress"

    # Customer listens on SSE (fan-out through Redis) while the operator replies.
    pubsub = container.redis.pubsub()
    await pubsub.subscribe(f"conv:{conv}")
    reply = await api.post(
        f"/v1/operator/cases/{case_id}/reply",
        json={
            "message": "Verifiquei sua conta: o repasse do crédito de ontem está agendado. Protocolo 7781.",
            "send_as_is": False,
        },
        headers=op,
    )
    assert reply.status_code == 200, reply.text
    out = reply.json()
    assert out["status"] == "human_replied"
    assert "7781" in out["customer_message"]
    got = None
    for _ in range(20):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
        if msg:
            got = json.loads(msg["data"])
            if got["type"] == "human_reply":
                break
    await pubsub.aclose()
    assert got and got["type"] == "human_reply" and got["case_id"] == case_id

    conv_doc = (await api.get(f"/v1/conversations/{conv}", headers=auth_headers())).json()
    roles = [t["role"] for t in conv_doc["turns"]]
    assert roles[-1] == "human_operator"
    assert conv_doc["status"] == "active"

    nxt = await api.post(
        "/v1/chat", json={"message": "Qual o status do meu chamado?", "conversation_id": conv}, headers=auth_headers()
    )
    assert nxt.status_code == 200
    state_slots = (await container.memory.get_owned("cliente1988", conv)).slots_json
    assert "7781" in (state_slots.get("last_human_reply") or "")
    assert nxt.json()["intent"] == "case_status"

    actions = await _audit_actions(container, case_id)
    for expected in ("case.created", "hitl.interrupt", "case.claimed", "hitl.resume_requested", "hitl.human_reply"):
        assert expected in actions


async def test_duplicate_reply_is_idempotent(api, container) -> None:  # type: ignore[no-untyped-def]
    body = await _escalate(api)
    case_id, conv = body["case_id"], body["conversation_id"]
    op = auth_headers(OP, OPERATOR_SCOPE)
    payload = {"message": "Resposta única.", "send_as_is": True}
    r1, r2 = await asyncio.gather(
        api.post(f"/v1/operator/cases/{case_id}/reply", json=payload, headers=op),
        api.post(f"/v1/operator/cases/{case_id}/reply", json=payload, headers=op),
    )
    assert {r1.status_code, r2.status_code} == {200}
    assert sorted([r1.json()["duplicate"], r2.json()["duplicate"]]) == [False, True]
    turns = (await api.get(f"/v1/conversations/{conv}", headers=auth_headers())).json()["turns"]
    assert sum(1 for t in turns if t["role"] == "human_operator") == 1


async def test_approval_approve_executes_with_case_owner_context(api, container) -> None:  # type: ignore[no-untyped-def]
    body = await _escalate(api, "Minha Get Smart não conecta de jeito nenhum, quero trocar a maquininha")
    case_id = body["case_id"]
    op = auth_headers(OP, OPERATOR_SCOPE)
    detail = (await api.get(f"/v1/operator/cases/{case_id}", headers=op)).json()
    assert detail["interrupt_kind"] == "approval"
    assert detail["proposed_action"]["tool"] == "request_terminal_replacement"
    assert detail["proposed_action"]["status"] == "proposed"
    # customer tokens can't decide approvals
    denied = await api.post(
        f"/v1/operator/cases/{case_id}/decision", json={"decision": "approve"}, headers=auth_headers()
    )
    assert denied.status_code == 403
    # reply-only operators can't approve either (scope separation)
    no_scope = await api.post(
        f"/v1/operator/cases/{case_id}/decision",
        json={"decision": "approve"},
        headers=auth_headers(OP, "cases:read cases:write"),
    )
    assert no_scope.status_code == 403
    ok = await api.post(
        f"/v1/operator/cases/{case_id}/decision", json={"decision": "approve", "note": "Troca aprovada."}, headers=op
    )
    assert ok.status_code == 200, ok.text
    res = ok.json()
    assert res["action_result"]["status"] == "executed"
    so_id = res["action_result"]["data"]["service_order_id"]
    async with container.db.session() as s:
        order = await s.get(ServiceOrder, so_id)
        action = await s.scalar(select(PendingAction).where(PendingAction.case_id == case_id))
    assert order is not None and order.customer_id == "cliente1988"
    assert action.status is ActionStatus.EXECUTED and action.decided_by == OP
    assert "approval.executed" in await _audit_actions(container, action.id)


async def test_approval_reject_is_explained_and_nothing_executes(api, container) -> None:  # type: ignore[no-untyped-def]
    body = await _escalate(api, "Minha Get Smart não conecta, quero trocar a maquininha agora")
    case_id = body["case_id"]
    op = auth_headers(OP, OPERATOR_SCOPE)
    async with container.db.session() as s:
        before = len((await s.scalars(select(ServiceOrder))).all())
    r = await api.post(
        f"/v1/operator/cases/{case_id}/decision",
        json={"decision": "reject", "note": "Faremos primeiro uma visita técnica."},
        headers=op,
    )
    assert r.status_code == 200
    assert r.json()["action_result"]["status"] == "rejected"
    assert "visita" in r.json()["customer_message"]
    async with container.db.session() as s:
        after = len((await s.scalars(select(ServiceOrder))).all())
        action = await s.scalar(select(PendingAction).where(PendingAction.case_id == case_id))
    assert before == after
    assert action.status is ActionStatus.REJECTED


async def test_approval_edit_cannot_target_another_customers_terminal(api, container) -> None:  # type: ignore[no-untyped-def]
    body = await _escalate(api, "Get Smart sem conexão desde ontem, preciso substituir a maquininha")
    case_id = body["case_id"]
    op = auth_headers(OP, OPERATOR_SCOPE)
    r = await api.post(
        f"/v1/operator/cases/{case_id}/decision",
        json={"decision": "edit", "edited_arguments": {"terminal_id": 2001}, "note": "trocar outro terminal"},
        headers=op,
    )
    assert r.status_code == 200, r.text
    assert (
        r.json()["action_result"]["status"] == "failed"
    )  # terminal 2001 belongs to cliente2077 → not found under owner ctx
    async with container.db.session() as s:
        orders = (await s.scalars(select(ServiceOrder).where(ServiceOrder.terminal_id == 2001))).all()
    assert orders == []


async def test_resume_survives_process_restart(api, container, settings) -> None:  # type: ignore[no-untyped-def]
    body = await _escalate(api)
    case_id = body["case_id"]
    # Simulate a restart: brand-new graph + checkpointer + service, nothing shared in memory.
    from app.container import build_container
    from app.llm.fake import FakeLLMProvider
    from tests.fakes import FakeWebSearch

    fresh = await build_container(settings, llm=FakeLLMProvider(), web=FakeWebSearch())
    try:
        out = await HitlService(fresh.db, fresh.chat).reply(case_id, OP, "Resposta após reinício.", True, "req_restart")
    finally:
        await fresh.close()
    assert out["status"] == "human_replied"
    assert out["customer_message"] == "Resposta após reinício."


async def test_customer_turn_while_waiting_does_not_break_resume(api) -> None:  # type: ignore[no-untyped-def]
    body = await _escalate(api)
    case_id, conv = body["case_id"], body["conversation_id"]
    mid = await api.post(
        "/v1/chat",
        json={"message": "Qual a diferença entre Get Smart e Get Clássica?", "conversation_id": conv},
        headers=auth_headers(),
    )
    assert mid.status_code == 200
    r = await api.post(
        f"/v1/operator/cases/{case_id}/reply",
        json={"message": "Retorno do especialista.", "send_as_is": True},
        headers=auth_headers(OP, OPERATOR_SCOPE),
    )
    assert r.status_code == 200
    assert r.json()["status"] == "human_replied"


async def test_sla_breach_bumps_priority_with_injected_clock(api, container, settings) -> None:  # type: ignore[no-untyped-def]
    body = await _escalate(api)
    case_id, conv = body["case_id"], body["conversation_id"]
    async with container.db.session() as s:
        case = await s.get(SupportCase, case_id)
        before_priority, due = case.priority.value, case.sla_due_at
    future = due + timedelta(minutes=1)
    breached = await check_sla(container.db, container.memory, settings, clock=lambda: future)
    assert case_id in breached
    async with container.db.session() as s:
        case = await s.get(SupportCase, case_id)
        notes = (await s.scalars(select(Outbox).where(Outbox.topic == "case.sla_breached"))).all()
    assert case.priority.value != before_priority
    assert case.sla_breaches == 1
    assert any(n.payload_json["case_id"] == case_id for n in notes)
    turns = (await api.get(f"/v1/conversations/{conv}", headers=auth_headers())).json()["turns"]
    assert turns[-1]["role"] == "system_note"
    assert "case.sla_breached" in await _audit_actions(container, case_id)


async def test_outbox_delivery_retry_and_dead_letter(container, settings) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import update

    ok_id, dead_id = f"CASE-T-{uuid.uuid4().hex[:8]}", f"CASE-D-{uuid.uuid4().hex[:8]}"
    async with container.db.transaction() as s:  # isolate from rows created by earlier tests
        await s.execute(
            update(Outbox).where(Outbox.status == OutboxStatus.PENDING).values(status=OutboxStatus.DELIVERED)
        )
        s.add(
            Outbox(
                topic="case.created", status=OutboxStatus.PENDING, payload_json={"case_id": ok_id, "priority": "high"}
            )
        )
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else httpx.Response(200)

    sink = Sink("https://hooks.example.test/teams", client=httpx.AsyncClient(transport=httpx.MockTransport(flaky)))
    now = datetime.now(UTC)
    first = await deliver_outbox(container.db, sink, settings, clock=lambda: now)
    assert first["retried"] >= 1
    later = now + timedelta(hours=1)
    second = await deliver_outbox(container.db, sink, settings, clock=lambda: later)
    assert second["delivered"] >= 1
    async with container.db.session() as s:
        row = await s.scalar(select(Outbox).where(Outbox.payload_json["case_id"].astext == ok_id))
    assert row.status is OutboxStatus.DELIVERED and row.attempts == 2

    async with container.db.transaction() as s:
        s.add(
            Outbox(
                topic="case.created",
                status=OutboxStatus.PENDING,
                payload_json={"case_id": dead_id, "priority": "normal"},
            )
        )
    dead_sink = Sink(
        "https://hooks.example.test/teams",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
    )
    t = now
    for _ in range(settings.outbox_max_attempts + 1):
        t = t + timedelta(hours=1)
        await deliver_outbox(container.db, dead_sink, settings, clock=lambda t=t: t)
    async with container.db.session() as s:
        dead = await s.scalar(select(Outbox).where(Outbox.payload_json["case_id"].astext == dead_id))
    assert dead.status is OutboxStatus.DEAD


async def test_operator_cannot_resume_without_operator_scope(api) -> None:  # type: ignore[no-untyped-def]
    body = await _escalate(api)
    r = await api.post(f"/v1/operator/cases/{body['case_id']}/reply", json={"message": "x"}, headers=auth_headers())
    assert r.status_code == 403
    r = await api.post(
        f"/v1/operator/cases/CASE-{uuid.uuid4().hex[:8]}/reply",
        json={"message": "x"},
        headers=auth_headers(OP, OPERATOR_SCOPE),
    )
    assert r.status_code == 404


async def test_explicit_replacement_request_is_proposed_even_if_llm_does_not(api, fake_llm) -> None:  # type: ignore[no-untyped-def]
    # MiniMax sometimes answers with diagnostics only; the explicit request still becomes an approval-gated proposal.
    fake_llm.script("customer_support", tool_call_response(("get_customer_terminals", {})), LLMResponse(content="ok"))
    body = await _escalate(api, "Get Smart sem conexão desde ontem, preciso substituir a maquininha")
    detail = (await api.get(f"/v1/operator/cases/{body['case_id']}", headers=auth_headers(OP, OPERATOR_SCOPE))).json()
    assert detail["interrupt_kind"] == "approval"
    assert detail["proposed_action"]["tool"] == "request_terminal_replacement"
    assert detail["proposed_action"]["status"] == "proposed"  # not executed until an operator approves


async def test_operator_queue_is_paginated_and_priority_ordered(api) -> None:  # type: ignore[no-untyped-def]
    await _escalate(api)
    await _escalate(api, "Fui vítima de fraude, alguém usou meu cartão na maquininha")
    op = auth_headers(OP, OPERATOR_SCOPE)
    first = (await api.get("/v1/operator/cases?limit=1", headers=op)).json()
    assert len(first["items"]) == 1 and first["next_offset"] == 1
    second = (await api.get("/v1/operator/cases?limit=1&offset=1", headers=op)).json()
    assert second["items"][0]["case_id"] != first["items"][0]["case_id"]
    page = (await api.get("/v1/operator/cases?limit=100", headers=op)).json()["items"]
    waiting = [c for c in page if c["awaiting_human"]]
    order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
    assert [order[c["priority"]] for c in waiting] == sorted(order[c["priority"]] for c in waiting)
    for bad in ("limit=0", "limit=1000", "offset=-1"):
        assert (await api.get(f"/v1/operator/cases?{bad}", headers=op)).status_code == 422


async def test_next_turn_can_repeat_numbers_from_the_operator_reply(api, fake_llm) -> None:  # type: ignore[no-untyped-def]
    # Found in manual validation: the operator's "R$ 59,90" was treated as an unsupported number, so the answer fell
    # back to a bare case list instead of telling the customer what the specialist said.
    body = await _escalate(api, "Quero falar com um atendente humano sobre uma cobrança")
    op = auth_headers(OP, OPERATOR_SCOPE)
    note = "Verificamos a cobrança: é o aluguel mensal da Get Smart, R$ 59,90."
    r = await api.post(
        f"/v1/operator/cases/{body['case_id']}/reply", json={"message": note, "send_as_is": True}, headers=op
    )
    assert r.status_code == 200, r.text
    fake_llm.script("synthesizer", "O especialista informou que a cobrança é o aluguel da Get Smart, R$ 59,90.")
    nxt = await api.post(
        "/v1/chat",
        json={"message": "O que o atendente disse sobre o meu chamado?", "conversation_id": body["conversation_id"]},
        headers=auth_headers(),
    )
    assert nxt.status_code == 200, nxt.text
    assert "59,90" in nxt.json()["answer"]


async def test_polished_reply_that_comments_on_the_operator_message_is_not_sent(api, container, fake_llm) -> None:  # type: ignore[no-untyped-def]
    # Found in manual testing: "Bom dia, em que posso ajudar?" reached the customer as "não foi possível identificar
    # um pedido específico nas notas do operador". The operator's own words must be delivered instead.
    body = await _escalate(api)
    fake_llm.script(
        "human_reply",
        "Infelizmente, não foi possível identificar um pedido específico nas notas do operador. As notas recebidas "
        'foram apenas: "Bom dia, em que posso ajudar?"',
    )
    op = auth_headers(OP, OPERATOR_SCOPE)
    r = await api.post(
        f"/v1/operator/cases/{body['case_id']}/reply", json={"message": "Bom dia, em que posso ajudar?"}, headers=op
    )
    assert r.status_code == 200, r.text
    conv = await api.get(f"/v1/conversations/{body['conversation_id']}", headers=auth_headers())
    human = [t["content"] for t in conv.json()["turns"] if t["role"] == "human_operator"]
    assert human == ["Bom dia, em que posso ajudar?"]
    assert fake_llm.calls_for("human_reply"), "the polishing step must have been attempted"
