"""Router Agent: rules → MiniMax structured output → deterministic fallback. Never answers the user."""

from __future__ import annotations

import time
from typing import Any

from langgraph.runtime import Runtime

from app.agents.prompts import render_messages
from app.errors import ProviderError
from app.llm.base import structured
from app.observability import metrics
from app.observability.cost import current_usage
from app.orchestration.planning import deterministic_plan, ready_steps, validate_plan
from app.orchestration.routing import (
    RouterOutput,
    derive_agents,
    detect_lang,
    fallback_route,
    mentioned_product,
    rule_route,
)
from app.orchestration.runtime import GraphContext, event, node
from app.orchestration.state import AgentState


def _slot_view(slots: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "active_terminal_model",
        "last_date_range",
        "open_case_id",
        "last_intent",
        "preferred_language",
        "troubleshooting_attempts",
    )
    return {k: slots.get(k) for k in keys if slots.get(k)}


@node("router")
async def router_node(state: AgentState, *, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    ctx = runtime.context
    message = state["message"]
    slots = dict(state.get("slots") or {})
    started = time.time()
    errors: list[str] = []
    rule = rule_route(message)
    if rule is not None:
        out = RouterOutput(
            intent=rule.intent,
            language=detect_lang(message, slots.get("preferred_language")),  # type: ignore[arg-type]
            knowledge_query=message,
            product=mentioned_product(message),
            **rule.flags,
        )
        source = "rule"
    else:
        try:
            out, _ = await structured(
                ctx.llm,
                task="router",
                messages=render_messages(
                    "router",
                    {"message": message, "slots": _slot_view(slots)},
                    history=state.get("history"),
                    summary=state.get("summary"),
                ),
                schema=RouterOutput,
                max_tokens=500,
            )
            source = "llm"
        except ProviderError as exc:
            out = fallback_route(message, slots)
            source = "fallback"
            errors.append(f"router:llm_failure:{exc.code}")
    # Short follow-ups keep the conversation language (deterministic, not the LLM's call).
    language = (
        out.language if len(message.split()) > 3 or not slots.get("preferred_language") else slots["preferred_language"]
    )
    flags: dict[str, Any] = {
        "requires_customer_data": out.requires_customer_data,
        "requires_getnet_knowledge": out.requires_getnet_knowledge,
        "requires_external_web": out.requires_external_web,
        "wants_human": out.wants_human,
        "date_reference": out.date_reference,
        "product": out.product or mentioned_product(message),
    }
    agents = derive_agents(out.intent, flags)
    usage = current_usage.get()
    budget_ok = usage is None or usage.total_tokens < usage.budget_tokens * 0.6
    if source == "llm":
        plan = validate_plan(
            out.plan,
            intent=out.intent,
            allowed_agents=agents,
            message=message,
            query=out.knowledge_query,
            product=flags["product"],
            date_reference=out.date_reference,
            max_steps=max(ctx.settings.supervisor_max_steps, len(agents)),
            budget_ok=budget_ok,
        )
    else:
        plan = deterministic_plan(
            out.intent, agents, message, out.knowledge_query, flags["product"], out.date_reference
        )
    active = ready_steps(plan, set())
    metrics.ROUTES.labels(source=source, intent=out.intent).inc()
    return {
        "intent": out.intent,
        "route": agents,
        "route_source": source,
        "route_flags": flags,
        "language": language,
        "plan": plan,
        "active_steps": active,
        "supervisor_steps": 0,
        "agent_visits": {s["agent"]: 1 for s in active},
        "errors": errors,
        "events": [
            event(
                "router",
                "routed",
                {"intent": out.intent, "route": agents, "source": source, "language": language},
                started,
            ),
            event(
                "router",
                "plan",
                {
                    "source": plan.get("source"),
                    "steps": [f"{s['id']}:{s['agent']}" for s in plan.get("steps", [])],
                    "rejected_reason": plan.get("rejected_reason"),
                    "first_superstep": [s["id"] for s in active],
                },
            ),
        ],
    }
