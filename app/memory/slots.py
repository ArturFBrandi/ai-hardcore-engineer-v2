"""Structured conversation slots — extracted deterministically from tool results, never from LLM prose.

Slots carry entities across turns (terminal, dates, transactions, open case, language, troubleshooting
attempts). On an intent-family change, topic-specific slots expire so a new topic is not polluted by
stale context.
"""

from __future__ import annotations

import re
from typing import Any

from app.orchestration.state import ConversationSlots, ToolResultEntry

INTENT_FAMILY = {
    "technical_support": "troubleshooting",
    "customer_terminal": "troubleshooting",
    "customer_transaction": "sales",
    "customer_settlement": "sales",
    "case_status": "case",
    "customer_account": "account",
    "getnet_product_information": "knowledge",
    "getnet_policy": "knowledge",
    "getnet_how_to": "knowledge",
    "weather": "external",
    "exchange_rate": "external",
    "general_information": "external",
    "human_support": "human",
    "unsupported": "other",
}

_FAILURE_REPORT = re.compile(
    r"\b(continua|ainda (nao|não)|nao (funcion|resolv|adiant|conect)|não (funcion|resolv|adiant|conect)|ja (reinici|tentei|fiz)|já (reinici|tentei|fiz)|"
    r"mesmo problema|igual|still|didn'?t work|not working|doesn'?t work|already (tried|restarted|rebooted)|same (issue|problem))\b",
    re.IGNORECASE,
)


def family(intent: str | None) -> str | None:
    return INTENT_FAMILY.get(intent or "")


def troubleshooting_category(
    message: str, tool_results: list[ToolResultEntry], previous: dict[str, int] | None = None
) -> str:
    text = message.lower()
    if previous and not re.search(
        r"conect|conex|internet|wi-?fi|sinal|signal|rede|network|chip|erro|error|adq|idl|bateria|tela|impress", text
    ):
        return max(previous, key=lambda k: previous[k])  # follow-up on the active topic ("continua igual")
    if re.search(r"conect|conex|internet|wi-?fi|sinal|signal|offline|rede|network|chip|4g|3g", text):
        return "connectivity"
    for r in tool_results:
        diag = ((r.get("data") or {}).get("diagnostics") or {}) if r.get("tool") == "get_terminal_status" else {}
        if diag.get("has_connectivity_issue"):
            return "connectivity"
    if re.search(r"erro|error|adq|idl|codigo|code", text):
        return "error_code"
    return "hardware"


def pick_terminal(message: str, tool_results: list[ToolResultEntry]) -> dict[str, Any] | None:
    """The terminal the conversation is about: the model the customer named, else the one with a
    connectivity issue, else the first one inspected."""
    statuses: list[dict[str, Any]] = [
        r["data"] or {}
        for r in tool_results
        if r.get("tool") == "get_terminal_status" and r.get("status") == "ok" and r.get("data")
    ]
    if not statuses:
        return None
    text = message.lower()
    named = [s for s in statuses if str(s.get("model", "")).lower().replace("á", "a") in text.replace("á", "a")]
    if named:
        return named[0]
    with_issue = [s for s in statuses if (s.get("diagnostics") or {}).get("has_connectivity_issue")]
    return (with_issue or statuses)[0]


def is_failure_report(message: str) -> bool:
    return bool(_FAILURE_REPORT.search(message))


def update_slots(
    previous: ConversationSlots,
    *,
    intent: str,
    language: str,
    message: str,
    tool_results: list[ToolResultEntry],
    case_id: str | None,
    evidence_ids: list[str],
) -> tuple[ConversationSlots, dict[str, Any]]:
    """Return the new slots and a small change log (for tracing)."""
    slots: ConversationSlots = dict(previous)  # type: ignore[assignment]
    changes: dict[str, Any] = {}
    fam = family(intent)
    prev_fam = previous.get("last_intent_family")

    # Topic switch → expire topic-specific slots (entities like the terminal survive for coreference).
    if prev_fam and fam and fam != prev_fam and fam not in ("human", "other"):
        if prev_fam == "troubleshooting":
            slots["troubleshooting_attempts"] = {}
            slots["troubleshooting_evidence_used"] = []
            changes["expired"] = "troubleshooting"
        if prev_fam == "sales" and fam != "case":
            slots["last_date_range"] = None
            slots["last_transaction_ids"] = []
            changes["expired"] = "sales"

    terminal = pick_terminal(message, tool_results)
    if terminal:
        slots["active_terminal_id"] = terminal.get("terminal_id")
        slots["active_terminal_model"] = terminal.get("model")
    for r in tool_results:
        if r.get("status") != "ok" or not r.get("data"):
            continue
        data = r["data"] or {}
        tool = r.get("tool")
        if tool == "get_terminal_status":
            continue  # handled by pick_terminal
        elif tool == "get_customer_terminals":
            terminals = data.get("terminals") or []
            if len(terminals) == 1 or not slots.get("active_terminal_id"):
                with_issue = terminals[0] if terminals else None
                if with_issue:
                    slots["active_terminal_id"] = with_issue.get("terminal_id")
                    slots["active_terminal_model"] = with_issue.get("model")
        elif tool == "get_recent_transactions":
            slots["last_transaction_ids"] = [t["transaction_id"] for t in (data.get("transactions") or [])[:5]]
            slots["last_date_range"] = data.get("period")
        elif tool == "get_settlement_schedule":
            slots["last_date_range"] = data.get("period")
        elif tool == "get_open_support_cases":
            cases = data.get("cases") or []
            if cases:
                slots["open_case_id"] = cases[0].get("case_id")
    if case_id:
        slots["open_case_id"] = case_id

    if fam == "troubleshooting":
        attempts = dict(slots.get("troubleshooting_attempts") or {})
        category = troubleshooting_category(message, tool_results, attempts)
        attempts[category] = attempts.get(category, 0) + 1
        slots["troubleshooting_attempts"] = attempts
        used = list(slots.get("troubleshooting_evidence_used") or [])
        slots["troubleshooting_evidence_used"] = list(dict.fromkeys([*used, *evidence_ids]))[-30:]
        changes["troubleshooting"] = {"category": category, "attempts": attempts[category]}

    slots["preferred_language"] = language
    slots["last_intent"] = intent
    if fam not in ("human", "other"):
        slots["last_intent_family"] = fam
    return slots, changes


def pending_attempts(slots: ConversationSlots, message: str) -> tuple[str | None, int]:
    """Attempts already recorded for the active troubleshooting category, *including* this turn if the
    message is a follow-up failure report on the same topic."""
    attempts = slots.get("troubleshooting_attempts") or {}
    if not attempts or slots.get("last_intent_family") != "troubleshooting":
        return None, 0
    category, count = max(attempts.items(), key=lambda kv: kv[1])
    return category, count + (1 if is_failure_report(message) else 0)
