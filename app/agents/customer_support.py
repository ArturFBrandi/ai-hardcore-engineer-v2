"""Customer Support Agent: bounded MiniMax tool-calling loop over scoped, typed, read-only tools.

After the LLM loop a deterministic *coverage guard* makes sure the data the decision model needs for the
intent is present (e.g. terminal diagnostics for troubleshooting, the settlement schedule for settlement
questions). If MiniMax is unavailable the guard alone provides the data — the agent degrades, not fails.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Mapping
from typing import Any

from langgraph.runtime import Runtime

from app.agents.formatting import describe_all
from app.agents.prompts import render_messages
from app.errors import ProviderError
from app.guardrails.normalize import canonical
from app.orchestration.runtime import GraphContext, event, node
from app.orchestration.state import AgentState, PlanStep, ProposedAction, ToolResultEntry
from app.tools.base import ToolResult
from app.tools.customer import br_today

PRESETS = {"today", "yesterday", "day_before_yesterday", "last_7_days", "last_30_days"}
REPLACEMENT_REQUEST = re.compile(
    r"\b(trocar|troca|substituir|substituicao|nova maquininha|outra maquininha|replace|replacement|new (card )?machine)\b"
)


def _entry(r: ToolResult, step_id: str) -> ToolResultEntry:
    return ToolResultEntry(
        tool=r.tool,
        status=r.status,
        data=r.data,
        error=r.error,
        retryable=r.retryable,
        latency_ms=r.latency_ms,
        arguments=dict(r.arguments),
        step_id=step_id,
    )


def required_calls(intent: str, date_ref: str | None, done: list[ToolResultEntry]) -> list[tuple[str, dict[str, Any]]]:
    """Deterministic minimum data per intent (code disposes)."""
    ok = {
        (t["tool"], json.dumps(t.get("arguments") or {}, sort_keys=True))
        for t in done
        if t.get("status") in ("ok", "not_found")
    }
    ok_tools = {t["tool"] for t in done if t.get("status") in ("ok", "not_found")}
    preset = date_ref if date_ref in PRESETS else None
    calls: list[tuple[str, dict[str, Any]]] = []
    if intent == "customer_settlement":
        if "get_settlement_schedule" not in ok_tools:
            calls.append(("get_settlement_schedule", {"by": "sales_date", "date_preset": preset or "yesterday"}))
        if "get_recent_transactions" not in ok_tools:
            calls.append(("get_recent_transactions", {"date_preset": preset or "yesterday"}))
    elif intent in ("technical_support", "customer_terminal"):
        if "get_customer_terminals" not in ok_tools:
            calls.append(("get_customer_terminals", {}))
        if (
            "get_recent_transactions",
            json.dumps({"date_preset": "today", "limit": 20}, sort_keys=True),
        ) not in ok and not any(
            t["tool"] == "get_recent_transactions" and (t.get("arguments") or {}).get("date_preset") == "today"
            for t in done
        ):
            calls.append(("get_recent_transactions", {"date_preset": "today", "limit": 20}))
    elif intent == "customer_transaction":
        if "get_recent_transactions" not in ok_tools and "get_transaction_details" not in ok_tools:
            calls.append(("get_recent_transactions", {"date_preset": preset or "last_7_days"}))
    elif intent == "case_status":
        if "get_open_support_cases" not in ok_tools:
            calls.append(("get_open_support_cases", {}))
    elif intent == "customer_account" and "get_customer_profile" not in ok_tools:
        calls.append(("get_customer_profile", {}))
    return calls


def replacement_target(message: str, slots: Mapping[str, Any], done: list[ToolResultEntry]) -> int | None:
    """Terminal for an explicit replacement request: the model named in the message, else the active one, else the only one.

    Returns None when the merchant did not ask for a replacement or the terminal is ambiguous (the operator decides).
    """
    text = canonical(message)
    if not REPLACEMENT_REQUEST.search(text):
        return None
    terminals = [
        term
        for t in done
        if t["tool"] == "get_customer_terminals" and t["status"] == "ok"
        for term in ((t.get("data") or {}).get("terminals") or [])
    ]
    named = [t for t in terminals if canonical(str(t.get("model", ""))) in text]
    if len({t["terminal_id"] for t in named}) == 1:
        return int(named[0]["terminal_id"])
    if slots.get("active_terminal_id") in {t["terminal_id"] for t in terminals}:
        return int(slots["active_terminal_id"])
    if len({t["terminal_id"] for t in terminals}) == 1:
        return int(terminals[0]["terminal_id"])
    return None


def terminal_status_calls(done: list[ToolResultEntry]) -> list[tuple[str, dict[str, Any]]]:
    fetched = {
        (t.get("data") or {}).get("terminal_id")
        for t in done
        if t["tool"] == "get_terminal_status" and t["status"] == "ok"
    }
    calls = []
    for t in done:
        if t["tool"] == "get_customer_terminals" and t["status"] == "ok":
            for term in ((t.get("data") or {}).get("terminals") or [])[:3]:
                if term["terminal_id"] not in fetched:
                    calls.append(("get_terminal_status", {"terminal_id": term["terminal_id"]}))
    return calls


async def run_step(state: AgentState, runtime: Runtime[GraphContext], step: PlanStep) -> dict[str, Any]:
    ctx = runtime.context
    s = ctx.settings
    started = time.time()
    intent = state.get("intent", "")
    inputs = step.get("inputs") or {}
    date_ref = inputs.get("date_reference") or (state.get("route_flags") or {}).get("date_reference")
    results: list[ToolResultEntry] = []
    proposed: list[ProposedAction] = []
    events = []
    errors: list[str] = []
    summary = ""
    slots = state.get("slots") or {}

    async def call(name: str, args: dict[str, Any]) -> ToolResult:
        t0 = time.time()
        r = await ctx.tools.invoke("customer_support", name, args, ctx.auth)
        events.append(
            event("customer_support", "tool_call", {"tool": name, "status": r.status, "args": r.arguments}, t0)
        )
        return r

    retry_tools = inputs.get("retry_tools")
    if retry_tools:
        # Supervisor-requested retry of transient failures: re-run the same calls, no LLM round needed.
        failed = [
            t for t in state.get("tool_results") or [] if t.get("status") == "error" and t.get("tool") in retry_tools
        ]
        for r in await asyncio.gather(*(call(t["tool"], t.get("arguments") or {}) for t in failed)):
            results.append(_entry(r, step["id"]))
    else:
        task_input = {
            "objective": step.get("objective"),
            "intent": intent,
            "date_reference": date_ref,
            "today": br_today().isoformat(),
            "language": state.get("language"),
            "message": state.get("message"),
            "slots": {
                k: slots.get(k)
                for k in (
                    "active_terminal_id",
                    "active_terminal_model",
                    "last_date_range",
                    "last_transaction_ids",
                    "open_case_id",
                    "troubleshooting_attempts",
                )
                if slots.get(k)
            },
        }
        messages = render_messages("customer_support", task_input, history=state.get("history"))
        tools = ctx.tools.schemas_for("customer_support")
        try:
            for _round in range(s.customer_support_max_tool_rounds):
                resp = await ctx.llm.chat(
                    task="customer_support", messages=messages, tools=tools, max_tokens=600, temperature=0.1
                )
                if not resp.tool_calls:
                    summary = resp.content
                    break
                messages.append(resp.assistant_message())
                outs = await asyncio.gather(
                    *(
                        call(
                            tc.name,
                            json.loads(tc.arguments) if _is_json(tc.arguments) else {"__invalid_json__": tc.arguments},
                        )
                        for tc in resp.tool_calls
                    )
                )
                for tc, r in zip(resp.tool_calls, outs, strict=True):
                    results.append(_entry(r, step["id"]))
                    if r.status == "approval_required":
                        proposed.append(
                            ProposedAction(
                                tool=r.tool,
                                arguments=dict(r.arguments),
                                policy="requires_approval",
                                reason=str(r.arguments.get("reason", "")),
                            )
                        )
                        content = json.dumps(
                            {"status": "proposal_recorded", "note": "requires human operator approval; not executed"}
                        )
                    elif r.ok:
                        content = json.dumps(r.data, ensure_ascii=False, default=str)[:6000]
                    else:
                        content = json.dumps({"status": r.status, "error": r.error})
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": content})
            else:
                events.append(
                    event("customer_support", "tool_round_limit", {"rounds": s.customer_support_max_tool_rounds})
                )
        except ProviderError as exc:
            errors.append(f"customer_support:llm_failure:{exc.code}")
            events.append(event("customer_support", "llm_failed", {"fallback": "coverage_guard"}))

    # Deterministic coverage guard.
    guard = required_calls(intent, date_ref, results + list(state.get("tool_results") or []))
    if guard:
        outs = await asyncio.gather(*(call(n, a) for n, a in guard))
        results.extend(_entry(r, step["id"]) for r in outs)
    if intent in ("technical_support", "customer_terminal"):
        more = terminal_status_calls(results + list(state.get("tool_results") or []))
        if more:
            outs = await asyncio.gather(*(call(n, a) for n, a in more))
            results.extend(_entry(r, step["id"]) for r in outs)
    if guard:
        events.append(event("customer_support", "coverage_guard", {"added_calls": [n for n, _ in guard]}))

    # Explicit replacement request the LLM did not turn into a proposal: code proposes, a human still approves.
    if intent in ("technical_support", "customer_terminal") and not proposed:
        target = replacement_target(state.get("message") or "", slots, results + list(state.get("tool_results") or []))
        if target is not None:
            r = await call(
                "request_terminal_replacement",
                {"terminal_id": target, "reason": "merchant explicitly requested a replacement; diagnostics attached"},
            )
            results.append(_entry(r, step["id"]))
            if r.status == "approval_required":
                proposed.append(
                    ProposedAction(
                        tool=r.tool,
                        arguments=dict(r.arguments),
                        policy="requires_approval",
                        reason=str(r.arguments.get("reason", "")),
                    )
                )
                events.append(event("customer_support", "replacement_proposed_by_policy", {"terminal_id": target}))

    status = "ok" if any(r["status"] == "ok" for r in results) else ("failed" if results else "no_data")
    if not summary:
        summary = describe_all(results, "en")
    return {
        "tool_results": results,
        "proposed_actions": proposed,
        "support_findings": [
            {
                "step_id": step["id"],
                "status": status,
                "summary": summary[:2000],
                "tools_called": [r["tool"] for r in results],
            }
        ],
        "completed_steps": [step["id"]],
        "errors": errors,
        "events": [
            *events,
            event(
                "customer_support", "step_done", {"step": step["id"], "status": status, "tools": len(results)}, started
            ),
        ],
    }


def _is_json(text: str) -> bool:
    try:
        json.loads(text or "{}")
    except json.JSONDecodeError:
        return False
    return True


@node("customer_support")
async def customer_support_node(state: AgentState, *, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    steps = [s for s in state.get("active_steps") or [] if s["agent"] == "customer_support"]
    merged: dict[str, Any] = {}
    for step in steps:  # usually one; sequential to keep tool rounds bounded per request
        out = await run_step(state, runtime, step)
        for k, v in out.items():
            merged[k] = [*merged.get(k, []), *v]
    return merged
