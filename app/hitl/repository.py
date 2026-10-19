"""Case / pending-action persistence used by the HITL nodes and the operator service."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.db.base import Database
from app.db.models import ActionStatus, ActorType, CaseStatus, PendingAction, SupportCase
from app.db.repositories import write_audit


async def get_case(db: Database, case_id: str) -> SupportCase | None:
    async with db.session() as s:
        return await s.get(SupportCase, case_id)


async def pending_action_for(db: Database, case_id: str) -> PendingAction | None:
    async with db.session() as s:
        return await s.scalar(
            select(PendingAction).where(PendingAction.case_id == case_id).order_by(PendingAction.created_at.desc())
        )


async def set_interrupt_checkpoint(db: Database, case_id: str, checkpoint_id: str, request_id: str | None) -> None:
    async with db.transaction() as s:
        case = await s.get(SupportCase, case_id, with_for_update=True)
        if case is None:
            return
        case.interrupt_checkpoint_id = checkpoint_id
        case.awaiting_human = True
        await write_audit(
            s,
            actor_type=ActorType.SYSTEM,
            actor_id="graph",
            action="hitl.interrupt",
            entity="support_case",
            entity_id=case_id,
            request_id=request_id,
            after={"checkpoint_id": checkpoint_id, "kind": case.interrupt_kind.value if case.interrupt_kind else None},
        )


async def record_action_decision(
    db: Database,
    action_id: str,
    *,
    status: ActionStatus,
    operator: str,
    result: dict[str, Any] | None,
    request_id: str | None,
    arguments: dict[str, Any] | None = None,
) -> None:
    async with db.transaction() as s:
        action = await s.get(PendingAction, action_id, with_for_update=True)
        if action is None:
            return
        before = {"status": action.status.value, "arguments": action.arguments_json}
        action.status = status
        action.decided_by = operator
        action.decided_at = datetime.now(UTC)
        action.result_json = result
        if arguments is not None:
            action.arguments_json = arguments
        await write_audit(
            s,
            actor_type=ActorType.OPERATOR,
            actor_id=operator,
            action=f"approval.{status.value}",
            entity="pending_action",
            entity_id=action_id,
            request_id=request_id,
            before=before,
            after={"status": status.value, "arguments": action.arguments_json, "result": result},
        )


async def mark_human_replied(
    db: Database, case_id: str, operator: str, request_id: str | None, message_preview: str
) -> None:
    async with db.transaction() as s:
        case = await s.get(SupportCase, case_id, with_for_update=True)
        if case is None:
            return
        before = {"status": case.status.value, "awaiting_human": case.awaiting_human}
        now = datetime.now(UTC)
        case.awaiting_human = False
        case.status = CaseStatus.AWAITING_CUSTOMER
        case.assigned_operator = case.assigned_operator or operator
        case.first_human_action_at = case.first_human_action_at or now
        case.updated_at = now
        await write_audit(
            s,
            actor_type=ActorType.OPERATOR,
            actor_id=operator,
            action="hitl.human_reply",
            entity="support_case",
            entity_id=case_id,
            request_id=request_id,
            before=before,
            after={"status": case.status.value, "message_preview": message_preview[:120]},
        )
