"""Deterministic pipeline nodes: memory load/save, input/output guardrails, supervisor, decision."""

from __future__ import annotations

import time
from typing import Any

from langgraph.runtime import Runtime

from app.agents.formatting import message
from app.agents.prompts import render_messages
from app.db.models import ConversationStatus, TurnRole
from app.errors import ProviderError
from app.guardrails.input import refusal, screen_input
from app.guardrails.output import check_output
from app.llm.base import structured
from app.memory.slots import family, pending_attempts, troubleshooting_category, update_slots
from app.memory.summary import needs_summary, summarize
from app.observability import metrics
from app.observability.cost import current_usage
from app.orchestration.decision import decide
from app.orchestration.routing import detect_lang
from app.orchestration.runtime import GraphContext, event, node
from app.orchestration.state import AgentState, ConversationSlots, Plan, PlanStep
from app.orchestration.supervisor import (
    SupervisorProposal,
    evaluate,
    is_ambiguous,
    llm_steps,
    proposal_to_step,
    step_fingerprint,
)


@node("load_memory")
async def load_memory_node(state: AgentState, *, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    ctx = runtime.context
    started = time.time()
    loaded = await ctx.memory.load(ctx.auth.customer_id, state["conversation_id"], ctx.settings.memory_window_turns)
    history = loaded["history"][-ctx.settings.memory_window_turns * 2 :]
    slots = loaded["slots"]
    return {
        "history": history,
        "summary": loaded["summary"],
        "slots": slots,
        "language": detect_lang(state["message"], slots.get("preferred_language")),
        "events": [
            event(
                "memory",
                "loaded",
                {
                    "turns": len(history),
                    "has_summary": bool(loaded["summary"]),
                    "slots": sorted(k for k, v in slots.items() if v),
                },
                started,
            )
        ],
    }


@node("input_guard")
async def input_guard_node(state: AgentState, *, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    ctx = runtime.context
    started = time.time()
    recent = [t["content"] for t in state.get("history") or [] if t.get("role") == "customer"]
    screen = screen_input(state["message"], recent, ctx.auth.customer_id)
    update: dict[str, Any] = {"guardrail_flags": screen.flags, "blocked": screen.blocked}
    if screen.blocked:
        lang = state.get("language", "pt")
        update.update(
            {
                "final_answer": refusal(screen.block_rule or "prompt_injection", lang),
                "candidate_answer": "",
                "intent": "unsupported",
                "route": [],
                "route_source": "guardrail",
                "status": "blocked",
                "confidence": 1.0,
            }
        )
    update["events"] = [
        event(
            "input_guard", "screened", {"flags": [f["rule"] for f in screen.flags], "blocked": screen.blocked}, started
        )
    ]
    return update


@node("supervisor")
async def supervisor_node(state: AgentState, *, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    ctx = runtime.context
    started = time.time()
    usage = current_usage.get()
    budget_ok = usage is None or usage.total_tokens < usage.budget_tokens * 0.8
    decision = evaluate(state, max_steps=ctx.settings.supervisor_max_steps, budget_ok=budget_ok)
    source = decision.source
    added = int(state.get("supervisor_steps") or 0)
    if (
        decision.decision == "synthesize"
        and ctx.settings.supervisor_llm_enabled
        and budget_ok
        and is_ambiguous(state)
        and added < ctx.settings.supervisor_max_steps
        and llm_steps(state) < ctx.settings.supervisor_max_llm_steps
    ):
        try:
            findings = {
                "intent": state.get("intent"),
                "question": state.get("message"),
                "customer_findings": [f.get("summary", "")[:400] for f in state.get("support_findings") or []],
                "knowledge": [
                    {"query": a.get("query"), "status": a.get("status"), "filters": a.get("filters")}
                    for a in state.get("knowledge_answers") or []
                ],
                "executed_steps": [
                    f"{s['agent']}:{s.get('objective', '')}" for s in (state.get("plan") or {}).get("steps", [])
                ],
            }
            proposal, _ = await structured(
                ctx.llm,
                task="supervisor",
                messages=render_messages("supervisor", findings),
                schema=SupervisorProposal,
                max_tokens=300,
            )
            executed = {step_fingerprint(s) for s in (state.get("plan") or {}).get("steps", [])}
            step = proposal_to_step(proposal, state)
            if proposal.decision == "escalate":
                decision.decision, decision.reason, source = "escalate", f"llm:{proposal.reason[:60]}", "llm"
            elif step is not None and step_fingerprint(step) not in executed:
                decision.decision, decision.reason, decision.steps, source = (
                    "continue",
                    f"llm:{proposal.reason[:60]}",
                    [step],
                    "llm",
                )
            else:
                source = "llm_rejected" if step is not None else "llm"
        except ProviderError:
            source = "deterministic(llm_unavailable)"
    plan: Plan = dict(state.get("plan") or {"steps": []})  # type: ignore[assignment]
    steps: list[PlanStep] = list(plan.get("steps", []))
    existing = {s["id"] for s in steps}
    for s in decision.steps:
        if s["id"] not in existing:
            steps.append(s)
    plan["steps"] = steps
    new_steps = [s for s in decision.steps if s.get("origin", "").startswith("supervisor")]
    visits = dict(state.get("agent_visits") or {})
    for s in decision.steps:
        visits[s["agent"]] = visits.get(s["agent"], 0) + 1
    metrics.SUPERVISOR_STEPS.labels(decision=decision.decision, reason=decision.reason.split(":")[0]).inc()
    detail = {
        "decision": decision.decision,
        "reason": decision.reason,
        "source": source,
        "steps": [f"{s['id']}:{s['agent']}" for s in decision.steps],
        **decision.detail,
    }
    return {
        "plan": plan,
        "active_steps": decision.steps if decision.decision == "continue" else [],
        "supervisor_steps": added + len(new_steps),
        "agent_visits": visits,
        "supervisor_decision": detail,
        "signals": decision.signals if decision.decision == "escalate" else [],
        "events": [event("supervisor", "decision", detail, started)],
    }


@node("decision")
async def decision_node(state: AgentState, *, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    ctx = runtime.context
    started = time.time()
    slots = state.get("slots") or {}
    attempts = 0
    if family(state.get("intent")) == "troubleshooting":
        prev = (
            (slots.get("troubleshooting_attempts") or {})
            if slots.get("last_intent_family") == "troubleshooting"
            else {}
        )
        category = troubleshooting_category(state.get("message", ""), list(state.get("tool_results") or []), prev)
        attempts = int(prev.get(category, 0)) + 1
        _, pending = pending_attempts(slots, state.get("message", ""))
        attempts = max(attempts, pending)
    d = decide(
        state,
        ctx.penalties,
        threshold=ctx.settings.escalation_confidence_threshold,
        max_attempts=ctx.settings.troubleshooting_max_attempts,
        rerank_min=ctx.settings.rag_rerank_min_score,
        attempts=attempts,
    )
    update: dict[str, Any] = {
        "confidence": d.confidence,
        "needs_escalation": d.needs_escalation,
        "signals": [s for s in d.signals if s not in (state.get("signals") or [])],
        "escalation_reasons": d.reasons,
        "priority": d.priority if d.needs_escalation else None,
        "interrupt_kind": d.interrupt_kind,
        "final_answer": state.get("candidate_answer", ""),
        "status": "answered",
        "events": [
            event(
                "decision_model",
                "evaluated",
                {
                    "confidence": d.confidence,
                    "needs_escalation": d.needs_escalation,
                    "signals": d.signals,
                    "priority": d.priority,
                    "suppressed": d.suppressed,
                    "attempts": attempts,
                },
                started,
            )
        ],
    }
    return update


@node("output_guard")
async def output_guard_node(state: AgentState, *, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    started = time.time()
    lang = state.get("language", "pt")
    text = state.get("final_answer") or state.get("candidate_answer") or message("no_evidence", lang)
    checked = check_output(text, language=lang)
    final = message("safe_block", lang) if checked.blocked else checked.text
    return {
        "final_answer": final,
        "guardrail_flags": checked.flags,
        "events": [
            event(
                "output_guard",
                "checked",
                {"flags": [f["rule"] for f in checked.flags], "blocked": checked.blocked},
                started,
            )
        ],
    }


@node("save_memory")
async def save_memory_node(state: AgentState, *, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    ctx = runtime.context
    started = time.time()
    conv_id = state["conversation_id"]
    turn = state.get("turn", 1)
    lang = state.get("language", "pt")
    status = state.get("status", "answered")
    agents = (
        [
            "router",
            *list(dict.fromkeys(s["agent"] for s in (state.get("plan") or {}).get("steps", []))),
            "supervisor",
            "synthesizer",
        ]
        if state.get("route")
        else []
    )
    sources = [dict(c) for c in state.get("citations") or []]
    await ctx.memory.append_turn(
        conv_id,
        turn=turn,
        role=TurnRole.CUSTOMER,
        content=state["message"],
        intent=state.get("intent"),
        request_id=state.get("request_id"),
    )
    await ctx.memory.append_turn(
        conv_id,
        turn=turn,
        role=TurnRole.ASSISTANT,
        content=state.get("final_answer", ""),
        intent=state.get("intent"),
        agents=agents,
        sources=sources,
        status=status,
        case_id=state.get("case_id"),
        request_id=state.get("request_id"),
    )
    evidence_ids = [
        e["chunk_id"]
        for e in state.get("retrieved_documents") or []
        if e["evidence_id"] in {c.get("evidence_id") for c in sources}
    ]
    if state.get("blocked"):
        slots: ConversationSlots = dict(state.get("slots") or {})  # type: ignore[assignment]
        changes: dict[str, Any] = {"skipped": "blocked_turn"}
    else:
        slots, changes = update_slots(
            dict(state.get("slots") or {}),  # type: ignore[arg-type]
            intent=state.get("intent", ""),
            language=lang,
            message=state["message"],
            tool_results=list(state.get("tool_results") or []),
            case_id=state.get("case_id"),
            evidence_ids=evidence_ids,
        )
    values: dict[str, Any] = {"slots_json": slots, "language": lang, "turn_count": turn}
    if state.get("case_id"):
        values["open_case_id"] = state["case_id"]
    values["status"] = ConversationStatus.AWAITING_HUMAN if status == "awaiting_human" else ConversationStatus.ACTIVE
    # Rolling summary when older turns exceed the token budget.
    summarized = False
    window = ctx.settings.memory_window_turns * 2
    history_rows = await ctx.memory.turns_for_summary(conv_id, upto_turn=turn)
    older = history_rows[:-window] if len(history_rows) > window else []
    if older and needs_summary(older, ctx.settings.memory_token_budget):
        values["summary_json"] = await summarize(ctx.llm, older, lang)
        values["summary_upto_turn"] = older[-1]["turn"]
        summarized = True
    await ctx.memory.update_conversation(conv_id, **values)
    await ctx.publisher.publish(
        conv_id,
        {
            "type": "assistant_message",
            "turn": turn,
            "status": status,
            "case_id": state.get("case_id"),
            "content": state.get("final_answer", ""),
        },
    )
    return {
        "slots": slots,
        "events": [
            event(
                "memory",
                "saved",
                {"turn": turn, "slot_changes": changes, "summarized": summarized, "status": status},
                started,
            )
        ],
    }
