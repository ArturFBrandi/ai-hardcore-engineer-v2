"""Write tools.

* ``create_support_case`` — Escalation agent only (executed by deterministic code). Idempotent per
  ``(conversation_id, escalation_reason_hash)`` through the unique ``idempotency_key``. Inserts the case,
  an optional pending action, an outbox notification and an audit entry in ONE transaction
  (transactional outbox).
* ``request_terminal_replacement`` — approval-gated. Customer Support may only *propose* it; it executes
  after an operator approves the persisted pending action, with the case owner's ``AuthContext``.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.models import (
    ActionPolicy,
    ActionStatus,
    ActorType,
    CaseCategory,
    CasePriority,
    CaseStatus,
    InterruptKind,
    Outbox,
    OutboxStatus,
    PendingAction,
    ServiceOrder,
    ServiceOrderStatus,
    SupportCase,
)
from app.db.repositories import CustomerDataRepository, next_business_id, next_case_id, write_audit
from app.errors import NotFoundError, ToolError
from app.tools.base import Tool, ToolInput
from app.tools.context import AuthContext


def escalation_idempotency_key(conversation_id: str, reasons: list[str]) -> str:
    digest = hashlib.sha256("|".join(sorted(set(reasons))).encode()).hexdigest()[:16]
    return f"{conversation_id}:{digest}"


class ProposedActionIn(ToolInput):
    tool: Literal["request_terminal_replacement"]
    arguments: dict[str, Any]


class CreateSupportCaseIn(ToolInput):
    conversation_id: str = Field(min_length=4, max_length=64)
    category: CaseCategory
    priority: CasePriority
    subject: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=3, max_length=4000)
    escalation_reasons: list[str] = Field(default_factory=list, max_length=20)
    context: dict[str, Any] = Field(default_factory=dict)
    interrupt_kind: InterruptKind = InterruptKind.REPLY
    sla_minutes: int = Field(ge=1, le=60 * 24 * 7)
    proposed_action: ProposedActionIn | None = None


class CreateSupportCase(Tool[CreateSupportCaseIn]):
    name = "create_support_case"
    description = "Create (idempotently) a support case for human follow-up."
    input_model = CreateSupportCaseIn

    async def run(self, ctx: AuthContext, args: CreateSupportCaseIn) -> dict[str, Any]:
        key = escalation_idempotency_key(args.conversation_id, args.escalation_reasons)
        now = datetime.now(UTC)
        async with self.db.transaction() as s:
            existing = await s.scalar(
                select(SupportCase).where(
                    SupportCase.idempotency_key == key, SupportCase.customer_id == ctx.customer_id
                )
            )
            if existing is not None:
                pending = await s.scalar(select(PendingAction.id).where(PendingAction.case_id == existing.id))
                return {
                    "case_id": existing.id,
                    "created": False,
                    "status": existing.status.value,
                    "priority": existing.priority.value,
                    "pending_action_id": pending,
                }
            case_id = await next_case_id(s, now)
            values = {
                "id": case_id,
                "customer_id": ctx.customer_id,
                "conversation_id": args.conversation_id,
                "category": args.category,
                "priority": args.priority,
                "status": CaseStatus.OPEN,
                "subject": args.subject,
                "description": args.description,
                "context_json": args.context,
                "escalation_reasons": args.escalation_reasons,
                "idempotency_key": key,
                "interrupt_kind": args.interrupt_kind,
                "awaiting_human": True,
                "sla_due_at": now + timedelta(minutes=args.sla_minutes),
            }
            # ON CONFLICT protects against two replicas racing on the same escalation.
            stmt = insert(SupportCase).values(**values).on_conflict_do_nothing(index_elements=["idempotency_key"])
            result = await s.execute(stmt.returning(SupportCase.id))
            inserted = result.scalar_one_or_none()
            if inserted is None:
                raise ToolError("concurrent escalation detected; retry", retryable=True)
            pending_id: str | None = None
            if args.proposed_action is not None:
                pending_id = await next_business_id(s, "ACT")
                s.add(
                    PendingAction(
                        id=pending_id,
                        case_id=case_id,
                        customer_id=ctx.customer_id,
                        tool=args.proposed_action.tool,
                        arguments_json=args.proposed_action.arguments,
                        policy=ActionPolicy.REQUIRES_APPROVAL,
                        status=ActionStatus.PROPOSED,
                    )
                )
            s.add(
                Outbox(
                    topic="case.created",
                    status=OutboxStatus.PENDING,
                    payload_json={
                        "case_id": case_id,
                        "category": args.category.value,
                        "priority": args.priority.value,
                        "subject": args.subject,
                        "interrupt_kind": args.interrupt_kind.value,
                        "reasons": args.escalation_reasons,
                    },
                )
            )
            await write_audit(
                s,
                actor_type=ActorType.AGENT,
                actor_id="escalation_agent",
                action="case.created",
                entity="support_case",
                entity_id=case_id,
                request_id=ctx.request_id,
                after={
                    "category": args.category.value,
                    "priority": args.priority.value,
                    "reasons": args.escalation_reasons,
                    "pending_action_id": pending_id,
                },
            )
        return {
            "case_id": case_id,
            "created": True,
            "status": CaseStatus.OPEN.value,
            "priority": args.priority.value,
            "pending_action_id": pending_id,
        }


class TerminalReplacementIn(ToolInput):
    terminal_id: int = Field(gt=0, description="The customer's terminal to replace (from get_customer_terminals).")
    reason: str = Field(min_length=5, max_length=500, description="Why a replacement is needed (diagnostics).")


class RequestTerminalReplacement(Tool[TerminalReplacementIn]):
    name = "request_terminal_replacement"
    description = (
        "PROPOSE replacing a malfunctioning card machine. Requires human operator approval; calling it only "
        "records a proposal. Use only after diagnostics show a persistent hardware/connectivity fault."
    )
    input_model = TerminalReplacementIn

    async def run(self, ctx: AuthContext, args: TerminalReplacementIn) -> dict[str, Any]:
        async with self.db.transaction() as s:
            terminal = await CustomerDataRepository(s, ctx).terminal(args.terminal_id)
            if terminal is None:
                raise NotFoundError("terminal not found")
            order_id = await next_business_id(s, "SO")
            s.add(
                ServiceOrder(
                    id=order_id,
                    customer_id=ctx.customer_id,
                    terminal_id=terminal.id,
                    kind="terminal_replacement",
                    reason=args.reason,
                    status=ServiceOrderStatus.REQUESTED,
                )
            )
            await write_audit(
                s,
                actor_type=ActorType.OPERATOR if ctx.actor_type == "operator" else ActorType.SYSTEM,
                actor_id=ctx.subject,
                action="action.executed",
                entity="service_order",
                entity_id=order_id,
                request_id=ctx.request_id,
                after={"tool": self.name, "terminal_id": terminal.id, "customer_id": ctx.customer_id},
            )
        return {
            "service_order_id": order_id,
            "terminal_id": terminal.id,
            "model": terminal.model,
            "status": ServiceOrderStatus.REQUESTED.value,
            "estimated_delivery_business_days": 3,
        }
