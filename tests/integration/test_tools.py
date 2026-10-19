"""Customer Support / Escalation tools against the real PostgreSQL schema and seed."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select, text

from app.config import Settings
from app.db.base import Database
from app.db.models import AuditLog, Outbox, PendingAction, ServiceOrder, SupportCase
from app.tools.context import AuthContext
from app.tools.registry import build_registry


@pytest.fixture
def registry(db: Database):  # type: ignore[no-untyped-def]
    return build_registry(db)


async def test_profile_valid_customer(registry, ctx_1988) -> None:  # type: ignore[no-untyped-def]
    r = await registry.invoke("customer_support", "get_customer_profile", "{}", ctx_1988)
    assert r.ok
    assert r.data["customer_id"] == "cliente1988"
    assert r.data["synthetic"] is True
    assert "456" in r.data["bank_account"] or "*" in r.data["bank_account"]


async def test_profile_unknown_customer_is_not_found(registry) -> None:  # type: ignore[no-untyped-def]
    ghost = AuthContext(customer_id="ghost-customer", subject="ghost-customer")
    r = await registry.invoke("customer_support", "get_customer_profile", {}, ghost)
    assert r.status == "not_found"


async def test_terminals_and_status_with_connectivity_events(registry, ctx_1988) -> None:  # type: ignore[no-untyped-def]
    r = await registry.invoke("customer_support", "get_customer_terminals", {}, ctx_1988)
    models = {t["model"] for t in r.data["terminals"]}
    assert models == {"Get Smart", "Get Mini"}
    s = await registry.invoke("customer_support", "get_terminal_status", {"terminal_id": 1001}, ctx_1988)
    assert s.ok
    assert s.data["diagnostics"]["connectivity_losses_24h"] >= 2
    assert s.data["diagnostics"]["has_connectivity_issue"] is True


async def test_absent_terminal_and_foreign_terminal_look_identical(registry, ctx_1988) -> None:  # type: ignore[no-untyped-def]
    missing = await registry.invoke("customer_support", "get_terminal_status", {"terminal_id": 999999}, ctx_1988)
    foreign = await registry.invoke("customer_support", "get_terminal_status", {"terminal_id": 2001}, ctx_1988)
    assert missing.status == foreign.status == "not_found"
    assert missing.error == foreign.error


async def test_yesterday_transactions_and_declined(registry, ctx_1988) -> None:  # type: ignore[no-untyped-def]
    r = await registry.invoke("customer_support", "get_recent_transactions", {"date_preset": "yesterday"}, ctx_1988)
    assert r.ok
    statuses = {t["status"] for t in r.data["transactions"]}
    assert "approved" in statuses and "declined" in statuses
    declined = next(t for t in r.data["transactions"] if t["status"] == "declined")
    assert declined["decline_code"] == "51"
    d = await registry.invoke(
        "customer_support", "get_transaction_details", {"transaction_id": declined["transaction_id"]}, ctx_1988
    )
    assert d.ok and d.data["decline_reason"]


async def test_no_transactions_in_empty_range(registry, ctx_1988) -> None:  # type: ignore[no-untyped-def]
    r = await registry.invoke(
        "customer_support",
        "get_recent_transactions",
        {"start_date": "2020-01-01", "end_date": "2020-01-31"},
        ctx_1988,
    )
    assert r.ok and r.data["returned"] == 0


@pytest.mark.parametrize(
    "args",
    [
        {"start_date": "2026-02-10", "end_date": "2026-01-01"},  # inverted
        {"start_date": "2025-01-01", "end_date": "2025-12-31"},  # > 90 days
        {"date_preset": "yesterday", "start_date": "2026-01-01"},  # ambiguous
        {"date_preset": "last_century"},  # unknown preset
        {"limit": 500},
        {"unexpected": True},
    ],
)
async def test_invalid_date_ranges_rejected(registry, ctx_1988, args) -> None:  # type: ignore[no-untyped-def]
    r = await registry.invoke("customer_support", "get_recent_transactions", args, ctx_1988)
    assert r.status == "invalid_arguments"


async def test_malformed_json_arguments(registry, ctx_1988) -> None:  # type: ignore[no-untyped-def]
    r = await registry.invoke("customer_support", "get_terminal_status", "{not json", ctx_1988)
    assert r.status == "invalid_arguments"


async def test_settlement_schedule_yesterday_has_scheduled_or_pending(registry, ctx_1988) -> None:  # type: ignore[no-untyped-def]
    r = await registry.invoke("customer_support", "get_settlement_schedule", {"date_preset": "yesterday"}, ctx_1988)
    assert r.ok
    statuses = {s["status"] for s in r.data["settlements"]}
    assert statuses & {"scheduled", "pending", "completed"}
    assert r.data["settlement_terms"]["credit_days"] == 2
    upcoming = await registry.invoke("customer_support", "get_settlement_schedule", {"by": "expected_date"}, ctx_1988)
    assert any(s["status"] == "scheduled" for s in upcoming.data["settlements"])


async def test_cross_customer_isolation_on_every_read_tool(registry, ctx_1988, ctx_2077) -> None:  # type: ignore[no-untyped-def]
    a = await registry.invoke("customer_support", "get_recent_transactions", {"date_preset": "last_30_days"}, ctx_2077)
    ids_2077 = {t["transaction_id"] for t in a.data["transactions"]}
    for tid in ids_2077:
        r = await registry.invoke("customer_support", "get_transaction_details", {"transaction_id": tid}, ctx_1988)
        assert r.status == "not_found"
    cases = await registry.invoke("customer_support", "get_open_support_cases", {}, ctx_1988)
    assert all(c["case_id"] != "CASE-SEED-0003" for c in cases.data["cases"])
    terms = await registry.invoke("customer_support", "get_customer_terminals", {}, ctx_1988)
    assert all(t["terminal_id"] != 2001 for t in terms.data["terminals"])


async def test_least_privilege_matrix(registry, ctx_1988) -> None:  # type: ignore[no-untyped-def]
    r = await registry.invoke("customer_support", "create_support_case", {}, ctx_1988)
    assert r.status == "forbidden"
    r = await registry.invoke("router", "get_customer_profile", {}, ctx_1988)
    assert r.status == "forbidden"
    r = await registry.invoke("customer_support", "refund_transaction", {}, ctx_1988)
    assert r.status == "forbidden"
    names = {s["function"]["name"] for s in registry.schemas_for("customer_support")}
    assert "create_support_case" not in names and "get_customer_profile" in names


async def test_approval_gated_action_not_executed_without_approval(registry, db, ctx_1988) -> None:  # type: ignore[no-untyped-def]
    async with db.session() as s:
        before = await s.scalar(select(func.count()).select_from(ServiceOrder))
    r = await registry.invoke(
        "customer_support",
        "request_terminal_replacement",
        {"terminal_id": 1001, "reason": "repeated connectivity loss"},
        ctx_1988,
    )
    assert r.status == "approval_required"
    no_scope = await registry.execute_approved(
        "request_terminal_replacement", {"terminal_id": 1001, "reason": "x" * 10}, ctx_1988
    )
    assert no_scope.status == "forbidden"
    async with db.session() as s:
        after = await s.scalar(select(func.count()).select_from(ServiceOrder))
    assert before == after


async def test_approved_action_executes_with_owner_context_only(registry, ctx_1988) -> None:  # type: ignore[no-untyped-def]
    owner_ctx = ctx_1988.for_case_owner("cliente1988", "operator.ana", "req_op")
    ok = await registry.execute_approved(
        "request_terminal_replacement", {"terminal_id": 1001, "reason": "approved replacement"}, owner_ctx
    )
    assert ok.ok and ok.data["service_order_id"].startswith("SO-")
    # A terminal owned by another customer cannot be targeted even with an approval scope.
    foreign = await registry.execute_approved(
        "request_terminal_replacement", {"terminal_id": 2001, "reason": "approved replacement"}, owner_ctx
    )
    assert foreign.status == "not_found"


async def test_create_support_case_idempotent_with_outbox_and_audit(registry, db, ctx_1988) -> None:  # type: ignore[no-untyped-def]
    conv = f"conv_{uuid.uuid4().hex[:12]}"
    args = {
        "conversation_id": conv,
        "category": "POS_CONNECTIVITY",
        "priority": "high",
        "subject": "Get Smart sem conexão",
        "description": "repeated troubleshooting failure",
        "escalation_reasons": ["repeated_troubleshooting_failure"],
        "sla_minutes": 30,
        "proposed_action": {
            "tool": "request_terminal_replacement",
            "arguments": {"terminal_id": 1001, "reason": "persistent fault"},
        },
        "interrupt_kind": "approval",
    }
    first = await registry.execute_system("create_support_case", args, ctx_1988)
    second = await registry.execute_system("create_support_case", args, ctx_1988)
    assert first.ok and second.ok
    assert first.data["created"] is True and second.data["created"] is False
    assert first.data["case_id"] == second.data["case_id"]
    assert first.data["case_id"].startswith("CASE-")
    async with db.session() as s:
        n_cases = await s.scalar(
            select(func.count()).select_from(SupportCase).where(SupportCase.conversation_id == conv)
        )
        n_outbox = await s.scalar(
            select(func.count())
            .select_from(Outbox)
            .where(Outbox.payload_json["case_id"].astext == first.data["case_id"])
        )
        n_audit = await s.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.entity_id == first.data["case_id"])
        )
        action = await s.scalar(select(PendingAction).where(PendingAction.case_id == first.data["case_id"]))
    assert (n_cases, n_outbox, n_audit) == (1, 1, 1)
    assert action is not None and action.status.value == "proposed"


async def test_audit_log_is_append_only(db) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError, match="append-only"):
        async with db.transaction() as s:
            await s.execute(text("UPDATE audit_log SET action = 'tampered'"))
    with pytest.raises(DBAPIError, match="append-only"):
        async with db.transaction() as s:
            await s.execute(text("DELETE FROM audit_log"))


async def test_database_failure_becomes_explicit_tool_error(settings: Settings, ctx_1988) -> None:  # type: ignore[no-untyped-def]
    broken = Database(settings.model_copy(update={"database_url": "postgresql+asyncpg://x:y@127.0.0.1:1/none"}))
    reg = build_registry(broken)
    r = await reg.invoke("customer_support", "get_customer_terminals", {}, ctx_1988)
    assert r.status == "error"
    assert r.retryable is True
    await broken.dispose()
