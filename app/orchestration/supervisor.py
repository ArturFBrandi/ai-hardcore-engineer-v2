"""Supervisor with bounded replanning.

Runs after every specialist superstep and chooses ``continue(steps) | synthesize | escalate``:

1. pending plan steps whose dependencies are now satisfied are scheduled, enriched with what the
   previous steps found (e.g. the terminal model found by Customer Support → ``product_filter``);
2. deterministic triggers add new steps (decline code → knowledge on that code; empty RAG under a strict
   filter → one broadened retrieval; transient tool failure → one retry; contradiction → escalate);
3. only when nothing deterministic applies and results are ambiguous, MiniMax proposes the next step,
   validated by code;
4. loop protection: ``SUPERVISOR_MAX_STEPS`` added steps, per-agent visit cap, no repeated identical
   step, token budget, LangGraph recursion limit. Termination is guaranteed: every iteration either
   schedules a *new* step (finite set, capped) or ends.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.orchestration.decision import settlement_contradiction
from app.orchestration.planning import ready_steps
from app.orchestration.state import AgentState, PlanStep

MAX_VISITS_PER_AGENT = 3
_DECLINE_WORDS = re.compile(r"negad|recusad|declin|nao autoriz|não autoriz|not authori")
DECLINE_CATEGORY = "sales_management"
TROUBLESHOOTING_CATEGORY = "terminal_troubleshooting"


class SupervisorProposal(BaseModel):
    decision: Literal["synthesize", "knowledge", "customer_support", "escalate"]
    reason: str = Field(default="", max_length=200)
    query: str | None = Field(default=None, max_length=300)
    product_filter: str | None = None
    category: str | None = None
    objective: str | None = Field(default=None, max_length=300)


@dataclass
class SupervisorDecision:
    decision: Literal["continue", "synthesize", "escalate"]
    reason: str
    steps: list[PlanStep] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    source: str = "deterministic"
    detail: dict[str, Any] = field(default_factory=dict)


def step_fingerprint(step: PlanStep) -> str:
    payload = json.dumps({"agent": step["agent"], "inputs": step.get("inputs", {})}, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode(), usedforsecurity=False).hexdigest()[:12]


def _terminal_model(state: AgentState) -> tuple[str | None, bool]:
    """Model of the terminal Customer Support identified (prefers one with a connectivity issue)."""
    best: tuple[str | None, bool] = (None, False)
    for t in state.get("tool_results") or []:
        data = t.get("data") or {}
        if t.get("status") != "ok":
            continue
        if t.get("tool") == "get_terminal_status":
            issue = bool((data.get("diagnostics") or {}).get("has_connectivity_issue"))
            if issue or best[0] is None:
                best = (data.get("model"), issue)
        elif t.get("tool") == "get_customer_terminals" and best[0] is None:
            terms = data.get("terminals") or []
            if terms:
                best = (terms[0].get("model"), False)
    if best[0] is None:
        best = ((state.get("slots") or {}).get("active_terminal_model"), False)
    return best


def _decline_codes(state: AgentState) -> list[tuple[str, str]]:
    codes = []
    for t in state.get("tool_results") or []:
        data = t.get("data") or {}
        rows = (
            data.get("transactions")
            if t.get("tool") == "get_recent_transactions"
            else [data]
            if t.get("tool") == "get_transaction_details"
            else []
        )
        for row in rows or []:
            if row.get("status") == "declined" and row.get("decline_code"):
                codes.append((row["decline_code"], row.get("decline_reason") or ""))
    return list(dict.fromkeys(codes))


def evaluate(state: AgentState, *, max_steps: int, budget_ok: bool) -> SupervisorDecision:
    plan = state.get("plan") or {}
    completed = set(state.get("completed_steps") or [])
    executed = {step_fingerprint(s) for s in plan.get("steps", []) if s["id"] in completed}
    added = int(state.get("supervisor_steps") or 0)
    visits = dict(state.get("agent_visits") or {})
    intent = state.get("intent", "")

    def can_add(step: PlanStep) -> bool:
        return (
            budget_ok
            and added + len(new) < max_steps
            and visits.get(step["agent"], 0) < MAX_VISITS_PER_AGENT
            and step_fingerprint(step) not in executed
            and step_fingerprint(step) not in {step_fingerprint(n) for n in new}
        )

    new: list[PlanStep] = []
    signals: list[str] = []
    next_id = len(plan.get("steps", []))

    # (0) contradiction between customer data and policy → escalate immediately.
    if settlement_contradiction(state.get("tool_results") or []):
        return SupervisorDecision("escalate", "contradictory_information", signals=["contradictory_information"])

    # (1) pending plan steps whose dependencies are satisfied — enrich with upstream findings.
    ready = ready_steps(plan, completed)
    if ready:
        scheduled: list[PlanStep] = []
        detail: dict[str, Any] = {"steps": [s["agent"] for s in ready]}
        reason = "plan_dependency_satisfied"
        for step in ready:
            enriched: PlanStep = dict(step)  # type: ignore[assignment]
            if step["agent"] == "knowledge" and intent == "technical_support":
                model, issue = _terminal_model(state)
                inputs = dict(step.get("inputs") or {})
                if model:
                    inputs["product_filter"] = model
                    inputs["category"] = TROUBLESHOOTING_CATEGORY
                    inputs["terminal_issue"] = "connectivity" if issue else "unknown"
                    enriched["objective"] = f"official troubleshooting steps for {model}"
                enriched["inputs"] = inputs
                reason = "terminal_model_known" if model else "terminal_model_unknown"
                detail["product_filter"] = model
            scheduled.append(enriched)
        return SupervisorDecision("continue", reason, steps=scheduled, detail=detail)

    # (2) deterministic triggers
    decline_relevant = intent == "customer_transaction" or bool(_DECLINE_WORDS.search(state.get("message", "").lower()))
    for code, reason_text in _decline_codes(state) if decline_relevant else []:
        step = PlanStep(
            id=f"s{next_id + len(new)}",
            agent="knowledge",
            objective=f"explain decline code {code}",
            inputs={
                "mode": "getnet",
                "query": f"venda negada código {code} {reason_text} não autorizada emissor o que fazer",
            },
            depends_on=[],
            origin="supervisor",
        )
        if not any(
            ka.get("query", "").startswith("venda negada código") for ka in state.get("knowledge_answers") or []
        ) and can_add(step):
            new.append(step)
            signals.append("decline_code_found")
            break

    for ka in state.get("knowledge_answers") or []:
        filt = ka.get("filters") or {}
        if (
            ka.get("mode") == "getnet"
            and ka.get("status") == "no_evidence"
            and (filt.get("product") or filt.get("category"))
            and not ka.get("broadened")
        ):
            step = PlanStep(
                id=f"s{next_id + len(new)}",
                agent="knowledge",
                objective="broadened retrieval without strict filters",
                inputs={
                    "mode": "getnet",
                    "query": ka.get("query", state.get("message", "")),
                    "broaden": True,
                    "product_hint": filt.get("product"),
                },
                depends_on=[],
                origin="supervisor",
            )
            if can_add(step):
                new.append(step)
                signals.append("rag_empty_under_filter")

    failed_retryable = [t for t in state.get("tool_results") or [] if t.get("status") == "error" and t.get("retryable")]
    if failed_retryable and visits.get("customer_support", 0) < 2:
        step = PlanStep(
            id=f"s{next_id + len(new)}",
            agent="customer_support",
            objective="retry after transient tool failure",
            inputs={"intent": intent, "retry_tools": sorted({t["tool"] for t in failed_retryable})},
            depends_on=[],
            origin="supervisor",
        )
        if can_add(step):
            new.append(step)
            signals.append("transient_tool_failure")

    if new:
        return SupervisorDecision("continue", signals[0], steps=new, signals=signals, detail={"added": len(new)})
    if added >= max_steps:
        return SupervisorDecision("synthesize", "step_budget_exhausted", detail={"supervisor_steps": added})
    return SupervisorDecision("synthesize", "results_sufficient")


def is_ambiguous(state: AgentState) -> bool:
    """When to ask the LLM for a proposal: deterministic rules found nothing but evidence is weak while
    the merchant's own data was retrieved (e.g. a policy question about their data with no RAG hit)."""
    answers = state.get("knowledge_answers") or []
    weak = any(a.get("mode") == "getnet" and a.get("status") in ("no_evidence", "insufficient") for a in answers)
    has_data = any(t.get("status") == "ok" for t in state.get("tool_results") or [])
    return weak and has_data


def proposal_to_step(p: SupervisorProposal, state: AgentState) -> PlanStep | None:
    next_id = len((state.get("plan") or {}).get("steps", []))
    if p.decision == "knowledge":
        inputs: dict[str, Any] = {"mode": "getnet", "query": p.query or state.get("message", "")}
        if p.product_filter:
            inputs["product_filter"] = p.product_filter
        if p.category:
            inputs["category"] = p.category
        return PlanStep(
            id=f"s{next_id}",
            agent="knowledge",
            objective=p.reason or "llm proposed retrieval",
            inputs=inputs,
            depends_on=[],
            origin="supervisor_llm",
        )
    if p.decision == "customer_support":
        return PlanStep(
            id=f"s{next_id}",
            agent="customer_support",
            objective=p.objective or p.reason or "llm proposed lookup",
            inputs={"intent": state.get("intent", "")},
            depends_on=[],
            origin="supervisor_llm",
        )
    return None
