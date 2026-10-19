"""Human Escalation Agent + the HITL nodes (``await_human`` interrupt, ``execute_action``, ``human_reply``).

Escalation: MiniMax suggests category/subject/summary (validated against the enum; deterministic fallback
guarantees escalation never fails because the LLM did), code builds the sanitized context, priority and
SLA, and calls ``create_support_case`` (idempotent, transactional outbox + audit). The customer gets a
deterministic handoff message with the case id. If the case cannot be persisted the customer is told so
honestly, with the official contact channels.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from app.agents.formatting import ACTION_LABEL, describe_all, message
from app.agents.prompts import render_messages
from app.db.models import ActionStatus, CaseCategory, TurnRole
from app.errors import ProviderError
from app.guardrails.output import check_output, unsupported_numbers
from app.guardrails.redaction import redact_text
from app.hitl import repository as hitl_repo
from app.llm.base import strip_reasoning, structured
from app.observability import metrics
from app.orchestration.decision import sla_minutes
from app.orchestration.runtime import GraphContext, event, node
from app.orchestration.state import AgentState

INTENT_CATEGORY = {
    "technical_support": CaseCategory.POS_CONNECTIVITY,
    "customer_terminal": CaseCategory.POS_HARDWARE,
    "customer_transaction": CaseCategory.TRANSACTION_ISSUE,
    "customer_settlement": CaseCategory.SETTLEMENT_ISSUE,
    "customer_account": CaseCategory.ACCOUNT_ISSUE,
    "case_status": CaseCategory.GENERAL_REQUEST,
}


class EscalationDraft(BaseModel):
    category: CaseCategory
    subject: str = Field(min_length=3, max_length=120)
    summary: str = Field(min_length=3, max_length=1200)


def fallback_category(state: AgentState, signals: list[str]) -> CaseCategory:
    if "high_risk_report" in signals:
        return CaseCategory.SECURITY_CONCERN
    intent = state.get("intent", "")
    if intent == "technical_support":
        has_conn = any(
            ((t.get("data") or {}).get("diagnostics") or {}).get("has_connectivity_issue")
            for t in state.get("tool_results") or []
        )
        return (
            CaseCategory.POS_CONNECTIVITY
            if has_conn or "conect" in state.get("message", "").lower()
            else CaseCategory.POS_HARDWARE
        )
    if intent.startswith("getnet_"):
        return CaseCategory.PRODUCT_QUESTION
    return INTENT_CATEGORY.get(intent, CaseCategory.GENERAL_REQUEST)


def sanitized_context(state: AgentState) -> dict[str, Any]:
    """Operator-facing execution context: decisions and summaries only — no secrets, no chain-of-thought."""
    tools = state.get("tool_results") or []
    return {
        "intent": state.get("intent"),
        "route": state.get("route"),
        "route_source": state.get("route_source"),
        "plan_steps": [
            f"{s['id']}:{s['agent']}:{s.get('objective', '')[:80]}" for s in (state.get("plan") or {}).get("steps", [])
        ],
        "signals": state.get("signals"),
        "confidence": state.get("confidence"),
        "tools_used": sorted({t["tool"] for t in tools if t.get("status") == "ok"}),
        "tools_failed": sorted({t["tool"] for t in tools if t.get("status") == "error"}),
        "customer_data": redact_text(describe_all(list(tools), "pt"))[:1500],
        "evidence": [
            {"title": e["title"], "url": e["url"], "score": e.get("rerank_score")}
            for e in (state.get("retrieved_documents") or [])[:5]
        ],
        "errors": state.get("errors"),
        "customer_message": redact_text(state.get("message", ""))[:500],
        "language": state.get("language"),
        "candidate_answer": redact_text(state.get("candidate_answer") or "")[:800],
    }


@node("escalation")
async def escalation_node(state: AgentState, *, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    ctx = runtime.context
    lang = state.get("language", "pt")
    started = time.perf_counter()
    signals = list(state.get("signals") or [])
    reasons = list(state.get("escalation_reasons") or [])
    context = sanitized_context(state)
    try:
        draft, _ = await structured(
            ctx.llm,
            task="escalation",
            messages=render_messages("escalation", {"reasons": reasons, "context": context}),
            schema=EscalationDraft,
            max_tokens=500,
        )
        category, subject, summary, source = draft.category, draft.subject, redact_text(draft.summary), "llm"
        if "high_risk_report" in signals:
            category = CaseCategory.SECURITY_CONCERN  # code overrides the LLM for safety-critical categories
    except ProviderError:
        category = fallback_category(state, signals)
        subject = f"{category.value}: {state.get('intent')}"[:120]
        summary = f"Escalation reasons: {', '.join(reasons)}. Customer message: {context['customer_message'][:300]}"
        source = "fallback"
    priority = state.get("priority") or "normal"
    proposed = next(iter(state.get("proposed_actions") or []), None)
    args = {
        "conversation_id": state["conversation_id"],
        "category": category.value,
        "priority": priority,
        "subject": subject,
        "description": summary,
        "escalation_reasons": reasons,
        "context": {**context, "summary_source": source},
        "interrupt_kind": state.get("interrupt_kind") or "reply",
        "sla_minutes": sla_minutes(priority, ctx.settings.hitl_sla_minutes),
        "proposed_action": {"tool": proposed["tool"], "arguments": proposed["arguments"]} if proposed else None,
    }
    result = await ctx.tools.execute_system("create_support_case", args, ctx.auth)
    for s in signals:
        metrics.ESCALATIONS.labels(reason=s).inc()
    if not result.ok:
        text = message("handoff_failed", lang)
        return {
            "final_answer": text,
            "status": "escalated",
            "case_id": None,
            "events": [
                event("escalation", "case_failed", {"error": result.error, "category": category.value}, started)
            ],
            "errors": ["escalation:case_persist_failed"],
        }
    case_id = result.data["case_id"]  # type: ignore[index]
    if proposed:
        label = ACTION_LABEL.get(proposed["tool"], {}).get(lang, proposed["tool"])
        handoff = message("handoff_approval", lang, action=label, case_id=case_id)
    else:
        handoff = message("handoff", lang, case_id=case_id, priority=priority)
    if "repeated_troubleshooting_failure" in signals:
        handoff = f"{message('repeated_failure', lang)} {handoff}"
    candidate = state.get("candidate_answer") or ""
    keep_answer = (
        bool(candidate)
        and ("approval_required" in signals)
        and not ({"explicit_human_request", "repeated_troubleshooting_failure"} & set(signals))
    )
    final = f"{candidate}\n\n{handoff}" if keep_answer else handoff
    return {
        "final_answer": final,
        "status": "awaiting_human",
        "case_id": case_id,
        "case_category": category.value,
        "pending_action_id": result.data.get("pending_action_id"),  # type: ignore[union-attr]
        "events": [
            event(
                "escalation",
                "case_created" if result.data.get("created") else "case_reused",  # type: ignore[union-attr]
                {
                    "case_id": case_id,
                    "category": category.value,
                    "priority": priority,
                    "reasons": reasons,
                    "summary_source": source,
                },
                started,
            )
        ],
    }


# ------------------------------------------------------------------------------------- HITL
class OperatorDecision(BaseModel):
    kind: Literal["reply", "approval"]
    operator_id: str
    message: str = ""
    send_as_is: bool = False
    decision: Literal["approve", "reject", "edit"] | None = None
    edited_arguments: dict[str, Any] | None = None
    note: str = ""
    request_id: str | None = None


@node("await_human")
async def await_human_node(state: AgentState, *, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    payload = {
        "case_id": state.get("case_id"),
        "kind": state.get("interrupt_kind") or "reply",
        "priority": state.get("priority"),
        "category": state.get("case_category"),
        "reasons": state.get("escalation_reasons"),
        "proposed_customer_message": state.get("final_answer"),
        "proposed_action": next(iter(state.get("proposed_actions") or []), None),
    }
    value = interrupt(payload)  # checkpointed; resumed with Command(resume=OperatorDecision)
    decision = OperatorDecision.model_validate(value)
    return {
        "operator_decision": decision.model_dump(),
        "events": [
            event(
                "await_human",
                "resumed",
                {"kind": decision.kind, "decision": decision.decision, "operator": decision.operator_id},
            )
        ],
    }


@node("execute_action")
async def execute_action_node(state: AgentState, *, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    ctx = runtime.context
    dec = OperatorDecision.model_validate(state.get("operator_decision") or {})
    case_id = state.get("case_id") or ""
    action = await hitl_repo.pending_action_for(ctx.db, case_id)
    started = time.perf_counter()
    if action is None or action.customer_id != ctx.auth.customer_id:
        return {
            "action_result": {"status": "missing"},
            "events": [event("execute_action", "no_pending_action", {"case_id": case_id}, started)],
        }
    if action.status is not ActionStatus.PROPOSED:
        return {
            "action_result": {"status": "already_decided", "previous": action.status.value},
            "events": [event("execute_action", "already_decided", {"status": action.status.value}, started)],
        }
    metrics.APPROVALS.labels(decision=dec.decision or "none").inc()
    if dec.decision == "reject":
        await hitl_repo.record_action_decision(
            ctx.db,
            action.id,
            status=ActionStatus.REJECTED,
            operator=dec.operator_id,
            result={"note": dec.note},
            request_id=dec.request_id,
        )
        return {
            "action_result": {"status": "rejected", "note": dec.note},
            "events": [event("execute_action", "rejected", {"action_id": action.id}, started)],
        }
    args = dict(action.arguments_json)
    if dec.decision == "edit" and dec.edited_arguments:
        args = {**args, **dec.edited_arguments}
    # Executes with the CASE OWNER's context (ctx.auth is built from the persisted case row by the HITL service).
    result = await ctx.tools.execute_approved(action.tool, args, ctx.auth)
    status = ActionStatus.EXECUTED if result.ok else ActionStatus.FAILED
    await hitl_repo.record_action_decision(
        ctx.db,
        action.id,
        status=status,
        operator=dec.operator_id,
        result=result.data or {"error": result.error},
        request_id=dec.request_id,
        arguments=args,
    )
    return {
        "action_result": {"status": status.value, "data": result.data, "error": result.error, "tool": action.tool},
        "tool_results": [
            {
                "tool": result.tool,
                "status": result.status,
                "data": result.data,
                "error": result.error,
                "retryable": False,
                "latency_ms": result.latency_ms,
                "arguments": dict(result.arguments),
                "step_id": "approval",
            }
        ],
        "events": [
            event(
                "execute_action",
                "executed" if result.ok else "failed",
                {"action_id": action.id, "tool": action.tool, "decision": dec.decision},
                started,
            )
        ],
    }


@node("human_reply")
async def human_reply_node(state: AgentState, *, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    ctx = runtime.context
    lang = state.get("language", "pt")
    dec = OperatorDecision.model_validate(state.get("operator_decision") or {})
    action_result = state.get("action_result")
    started = time.perf_counter()
    operator_text = dec.message or dec.note
    if dec.kind == "approval" and not operator_text:
        operator_text = {
            ("approve", "pt"): "Sua solicitação foi aprovada.",
            ("approve", "en"): "Your request was approved.",
            ("edit", "pt"): "Sua solicitação foi aprovada com ajustes.",
            ("edit", "en"): "Your request was approved with adjustments.",
            ("reject", "pt"): "Sua solicitação não foi aprovada.",
            ("reject", "en"): "Your request was not approved.",
        }.get((dec.decision or "approve", "pt" if lang == "pt" else "en"), "")
    grounding = f"{operator_text}\n{action_result or ''}"
    source = "verbatim"
    text = operator_text
    if not dec.send_as_is:
        try:
            resp = await ctx.llm.chat(
                task="human_reply",
                messages=render_messages(
                    "human_reply",
                    {
                        "language": lang,
                        "operator_notes": operator_text,
                        "decision": dec.decision,
                        "action_result": action_result,
                        "case_id": state.get("case_id"),
                    },
                ),
                max_tokens=400,
                temperature=0.2,
            )
            candidate = strip_reasoning(resp.content)
            if candidate and not unsupported_numbers(candidate, grounding + f" {state.get('case_id')}"):
                text, source = candidate, "llm"
        except ProviderError:
            source = "verbatim_fallback"
    checked = check_output(text, language=lang)
    text = checked.text if not checked.blocked else operator_text
    case_id = state.get("case_id") or ""
    await ctx.memory.append_turn(
        state["conversation_id"],
        turn=state.get("turn", 0),
        role=TurnRole.HUMAN_OPERATOR,
        content=text,
        case_id=case_id,
        status="human_replied",
        request_id=dec.request_id,
    )
    slots = dict(state.get("slots") or {})
    slots["last_human_reply"] = redact_text(text)[:300]
    await ctx.memory.update_conversation(state["conversation_id"], status="active", slots_json=slots)
    await hitl_repo.mark_human_replied(ctx.db, case_id, dec.operator_id, dec.request_id, text)
    await ctx.publisher.publish(
        state["conversation_id"],
        {"type": "human_reply", "case_id": case_id, "content": text, "turn": state.get("turn")},
    )
    return {
        "final_answer": text,
        "human_message": text,
        "status": "human_replied",
        "slots": slots,
        "guardrail_flags": checked.flags,
        "events": [event("human_reply", "delivered", {"source": source, "case_id": case_id}, started)],
    }
