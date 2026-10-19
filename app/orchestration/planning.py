"""Typed plans: the LLM proposes, code validates.

Validation: allowed agents only (those derived by code for the intent), allowed transitions,
``SUPERVISOR_MAX_STEPS`` cap, no repeated identical step, dependencies must point backwards, and the
token budget must not be exhausted. An invalid proposal is replaced by the deterministic plan for the
intent (``source = deterministic``).
"""

from __future__ import annotations

from typing import Any

from app.orchestration.routing import WEB_INTENTS, PlanProposal
from app.orchestration.state import Plan, PlanStep

DEFAULT_OBJECTIVES = {
    "customer_support": "retrieve the merchant's data relevant to the request",
    "knowledge": "find official Getnet information relevant to the request",
}


def _step(
    idx: int, agent: str, objective: str, inputs: dict[str, Any], depends_on: list[str], origin: str = "router"
) -> PlanStep:
    return PlanStep(id=f"s{idx}", agent=agent, objective=objective, inputs=inputs, depends_on=depends_on, origin=origin)


def knowledge_inputs(intent: str, message: str, query: str | None, product: str | None) -> dict[str, Any]:
    mode = "web" if intent in WEB_INTENTS else "getnet"
    inputs: dict[str, Any] = {"mode": mode, "query": (query or message) if mode == "getnet" else message}
    if product and mode == "getnet":
        inputs["product_hint"] = product
    return inputs


def deterministic_plan(
    intent: str, agents: list[str], message: str, query: str | None, product: str | None, date_reference: str | None
) -> Plan:
    steps: list[PlanStep] = []
    cs_inputs = {"intent": intent, "date_reference": date_reference}
    if "customer_support" in agents:
        steps.append(_step(len(steps), "customer_support", DEFAULT_OBJECTIVES["customer_support"], cs_inputs, []))
    if "knowledge" in agents:
        # Troubleshooting depends on the terminal model found by Customer Support (supervisor dependency).
        depends = ["s0"] if intent == "technical_support" and steps else []
        steps.append(
            _step(
                len(steps),
                "knowledge",
                DEFAULT_OBJECTIVES["knowledge"],
                knowledge_inputs(intent, message, query, product),
                depends,
            )
        )
    return Plan(steps=steps, source="deterministic", rejected_reason=None)


def validate_plan(
    proposal: list[PlanProposal],
    *,
    intent: str,
    allowed_agents: list[str],
    message: str,
    query: str | None,
    product: str | None,
    date_reference: str | None,
    max_steps: int,
    budget_ok: bool,
) -> Plan:
    fallback = deterministic_plan(intent, allowed_agents, message, query, product, date_reference)

    def reject(reason: str) -> Plan:
        return Plan(steps=fallback["steps"], source="deterministic", rejected_reason=reason)

    if not proposal:
        return reject("empty_proposal") if allowed_agents else fallback
    if not budget_ok:
        return reject("token_budget")
    if len(proposal) > max_steps:
        return reject("too_many_steps")
    seen: set[tuple[str, str]] = set()
    steps: list[PlanStep] = []
    for idx, p in enumerate(proposal):
        if p.agent not in allowed_agents:
            return reject(f"agent_not_allowed:{p.agent}")
        key = (p.agent, p.objective.strip().lower())
        if key in seen:
            return reject("repeated_step")
        seen.add(key)
        if any(d >= idx or d < 0 for d in p.depends_on):
            return reject("invalid_dependency")
        inputs = (
            {"intent": intent, "date_reference": date_reference}
            if p.agent == "customer_support"
            else knowledge_inputs(intent, message, query, product)
        )
        steps.append(
            _step(idx, p.agent, p.objective or DEFAULT_OBJECTIVES[p.agent], inputs, [f"s{d}" for d in p.depends_on])
        )
    # Code-owned invariants: every agent derived for the intent is covered; troubleshooting keeps the dependency.
    missing = set(allowed_agents) - {s["agent"] for s in steps}
    if missing:
        return reject(f"missing_agents:{','.join(sorted(missing))}")
    if intent == "technical_support":
        ks = next(s for s in steps if s["agent"] == "knowledge")
        cs = next(s for s in steps if s["agent"] == "customer_support")
        if cs["id"] not in ks.get("depends_on", []):
            ks["depends_on"] = [cs["id"]]
    return Plan(steps=steps, source="llm", rejected_reason=None)


def ready_steps(plan: Plan, completed: set[str], running: set[str] | None = None) -> list[PlanStep]:
    running = running or set()
    return [
        s
        for s in plan.get("steps", [])
        if s["id"] not in completed and s["id"] not in running and set(s.get("depends_on", [])) <= completed
    ]
