"""Operator-side HITL use cases: queue, case detail, claim, reply, approval decision, resolve.

* The case row is the source of truth for ownership: resumes always run with the CASE OWNER's
  ``AuthContext`` (built here from the persisted row), so an operator can never act on another customer.
* Resume targets the interrupt checkpoint stored on the case (same thread), under the conversation lock.
* Double submits are safe: the ``awaiting_human`` flag is checked under a row lock; a duplicate returns the
  stored outcome (``duplicate: true``) and never executes twice.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.db.base import Database
from app.db.models import (
    ActorType,
    CasePriority,
    CaseStatus,
    ConversationTurn,
    InterruptKind,
    PendingAction,
    SupportCase,
)
from app.db.repositories import write_audit
from app.errors import ConflictError, NotFoundError
from app.observability import metrics
from app.services.chat import ChatService
from app.tools.context import AuthContext

PRIORITY_ORDER = {"urgent": 0, "high": 1, "normal": 2, "low": 3}


def case_summary(c: SupportCase) -> dict[str, Any]:
    return {
        "case_id": c.id,
        "customer_id": c.customer_id,
        "conversation_id": c.conversation_id,
        "category": c.category.value,
        "priority": c.priority.value,
        "status": c.status.value,
        "subject": c.subject,
        "awaiting_human": c.awaiting_human,
        "interrupt_kind": c.interrupt_kind.value if c.interrupt_kind else None,
        "assigned_operator": c.assigned_operator,
        "sla_due_at": c.sla_due_at.isoformat() if c.sla_due_at else None,
        "sla_breaches": c.sla_breaches,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
        "escalation_reasons": c.escalation_reasons,
    }


class HitlService:
    def __init__(self, db: Database, chat: ChatService) -> None:
        self.db = db
        self.chat = chat

    async def queue(self, status: str | None, priority: str | None, limit: int = 50) -> list[dict[str, Any]]:
        async with self.db.session() as s:
            stmt = select(SupportCase)
            if status:
                stmt = stmt.where(SupportCase.status == CaseStatus(status))
            if priority:
                stmt = stmt.where(SupportCase.priority == CasePriority(priority))
            rows = (await s.scalars(stmt.order_by(SupportCase.created_at.desc()).limit(limit))).all()
            awaiting = await s.scalar(
                select(func.count()).select_from(SupportCase).where(SupportCase.awaiting_human.is_(True))
            )
        metrics.HITL_QUEUE.set(int(awaiting or 0))
        return sorted(
            (case_summary(r) for r in rows),
            key=lambda c: (not c["awaiting_human"], PRIORITY_ORDER.get(c["priority"], 9)),
        )

    async def detail(self, case_id: str) -> dict[str, Any]:
        async with self.db.session() as s:
            case = await s.get(SupportCase, case_id)
            if case is None:
                raise NotFoundError("case not found")
            action = await s.scalar(
                select(PendingAction).where(PendingAction.case_id == case_id).order_by(PendingAction.created_at.desc())
            )
            excerpt: list[dict[str, Any]] = []
            if case.conversation_id:
                turns = (
                    await s.scalars(
                        select(ConversationTurn)
                        .where(ConversationTurn.conversation_id == case.conversation_id)
                        .order_by(ConversationTurn.id.desc())
                        .limit(10)
                    )
                ).all()
                excerpt = [
                    {
                        "turn": t.turn,
                        "role": t.role.value,
                        "content": t.content[:600],
                        "created_at": t.created_at.isoformat(),
                    }
                    for t in reversed(turns)
                ]
        return {
            **case_summary(case),
            "description": case.description,
            "context": case.context_json,  # sanitized at creation (no secrets / chain-of-thought)
            "conversation_excerpt": excerpt,
            "proposed_action": None
            if action is None
            else {
                "action_id": action.id,
                "tool": action.tool,
                "arguments": action.arguments_json,
                "status": action.status.value,
                "policy": action.policy.value,
            },
            "resolution_code": case.resolution_code,
        }

    async def claim(self, case_id: str, operator: str, request_id: str) -> dict[str, Any]:
        async with self.db.transaction() as s:
            case = await s.get(SupportCase, case_id, with_for_update=True)
            if case is None:
                raise NotFoundError("case not found")
            if case.assigned_operator and case.assigned_operator != operator and case.status is CaseStatus.IN_PROGRESS:
                raise ConflictError(f"case already claimed by {case.assigned_operator}")
            before = {"status": case.status.value, "assigned_operator": case.assigned_operator}
            case.assigned_operator = operator
            if case.status is CaseStatus.OPEN:
                case.status = CaseStatus.IN_PROGRESS
            case.first_human_action_at = case.first_human_action_at or datetime.now(UTC)
            case.updated_at = datetime.now(UTC)
            await write_audit(
                s,
                actor_type=ActorType.OPERATOR,
                actor_id=operator,
                action="case.claimed",
                entity="support_case",
                entity_id=case_id,
                request_id=request_id,
                before=before,
                after={"status": case.status.value, "assigned_operator": operator},
            )
            if case.created_at:
                metrics.HITL_TIME_TO_REPLY.observe(max(0.0, (datetime.now(UTC) - case.created_at).total_seconds()))
            return case_summary(case)

    async def _resume(
        self, case_id: str, decision: dict[str, Any], operator: str, request_id: str, expected_kind: InterruptKind
    ) -> dict[str, Any]:
        async with self.db.session() as s:
            peek = await s.get(SupportCase, case_id)
        if peek is None:
            raise NotFoundError("case not found")
        if peek.conversation_id is None:
            raise ConflictError("case has no conversation to resume")
        # Serialize with customer turns AND concurrent operator submits on the same conversation; the
        # awaiting_human check happens inside the lock, so a double submit can never resume twice.
        async with self.chat.memory.conversation_lock(peek.conversation_id):
            async with self.db.transaction() as s:
                case = await s.get(SupportCase, case_id, with_for_update=True)
                assert case is not None  # noqa: S101 - existed above; cases are never deleted
                if not case.awaiting_human or not case.interrupt_checkpoint_id:
                    last = (case.context_json or {}).get("last_resume")
                    if last:
                        return {**last, "duplicate": True}
                    raise ConflictError("case is not awaiting a human decision")
                if case.interrupt_kind is not expected_kind:
                    raise ConflictError(
                        f"case expects a {case.interrupt_kind.value if case.interrupt_kind else 'none'} decision"
                    )
                owner, conversation_id, checkpoint_id = (
                    case.customer_id,
                    peek.conversation_id,
                    case.interrupt_checkpoint_id,
                )
                await write_audit(
                    s,
                    actor_type=ActorType.OPERATOR,
                    actor_id=operator,
                    action="hitl.resume_requested",
                    entity="support_case",
                    entity_id=case_id,
                    request_id=request_id,
                    after={"kind": expected_kind.value, "decision": decision.get("decision")},
                )
            owner_ctx = AuthContext(customer_id=owner, subject=owner, actor_type="customer").for_case_owner(
                owner, operator, request_id
            )
            state = await self.chat.resume(
                owner_ctx,
                conversation_id,
                checkpoint_id,
                {**decision, "operator_id": operator, "request_id": request_id},
                request_id,
                lock=False,
            )
            outcome = {
                "case_id": case_id,
                "status": state.get("status"),
                "customer_message": state.get("human_message"),
                "action_result": state.get("action_result"),
            }
            async with self.db.transaction() as s:
                case = await s.get(SupportCase, case_id, with_for_update=True)
                if case is not None:
                    case.context_json = {**(case.context_json or {}), "last_resume": outcome}
                    case.awaiting_human = False
        return {**outcome, "duplicate": False}

    async def reply(
        self, case_id: str, operator: str, message: str, send_as_is: bool, request_id: str
    ) -> dict[str, Any]:
        return await self._resume(
            case_id,
            {"kind": "reply", "message": message, "send_as_is": send_as_is},
            operator,
            request_id,
            InterruptKind.REPLY,
        )

    async def decide(
        self,
        case_id: str,
        operator: str,
        decision: str,
        edited_arguments: dict[str, Any] | None,
        note: str,
        request_id: str,
    ) -> dict[str, Any]:
        payload = {
            "kind": "approval",
            "decision": decision,
            "edited_arguments": edited_arguments,
            "note": note,
            "message": note,
        }
        return await self._resume(case_id, payload, operator, request_id, InterruptKind.APPROVAL)

    async def resolve(
        self, case_id: str, operator: str, resolution_code: str, note: str, request_id: str
    ) -> dict[str, Any]:
        async with self.db.transaction() as s:
            case = await s.get(SupportCase, case_id, with_for_update=True)
            if case is None:
                raise NotFoundError("case not found")
            before = {"status": case.status.value}
            case.status = CaseStatus.RESOLVED
            case.awaiting_human = False
            case.resolution_code = resolution_code
            case.resolved_at = datetime.now(UTC)
            case.updated_at = datetime.now(UTC)
            case.context_json = {**(case.context_json or {}), "resolution_note": note[:2000]}
            await write_audit(
                s,
                actor_type=ActorType.OPERATOR,
                actor_id=operator,
                action="case.resolved",
                entity="support_case",
                entity_id=case_id,
                request_id=request_id,
                before=before,
                after={"status": "resolved", "resolution_code": resolution_code},
            )
            return case_summary(case)
