"""Deterministic confidence / escalation / priority model.

Confidence starts at 1.0 and is reduced by concrete *system signals* (never by the LLM's self-assessment).
Penalties and the threshold are configuration (validated YAML + env). Every decision is explainable via
``escalation_reasons``. Priority and SLA are deterministic functions of the signals.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from app.guardrails.normalize import canonical
from app.memory.slots import family
from app.orchestration.routing import PRODUCTS, mentioned_product
from app.orchestration.state import AgentState

KNOWN_SIGNALS = {
    "no_rag_evidence",
    "low_retrieval_score",
    "entity_mismatch",
    "contradictory_information",
    "customer_data_unavailable",
    "tool_failure",
    "llm_failure",
    "web_search_failure",
    "guardrail_flag",
    "unsupported_numeric_claim",
    "insufficient_evidence",
    "explicit_human_request",
    "repeated_troubleshooting_failure",
    "high_risk_report",
    "approval_required",
    "unsupported_request",
}
# Intents whose answer IS official Getnet evidence (product facts, troubleshooting steps). For customer-data
# intents (sales, settlements, cases) the merchant's own data is the answer and policy evidence only enriches it.
EVIDENCE_CORE_INTENTS = {"getnet_product_information", "getnet_policy", "getnet_how_to", "technical_support"}
EVIDENCE_SIGNALS = {"no_rag_evidence", "low_retrieval_score", "entity_mismatch", "insufficient_evidence"}
REASON_TEXT = {
    "no_rag_evidence": "no official evidence found",
    "low_retrieval_score": "low retrieval relevance",
    "entity_mismatch": "question names a product/entity absent from the evidence",
    "contradictory_information": "contradictory information between customer data and policy",
    "customer_data_unavailable": "required customer data unavailable",
    "tool_failure": "a customer-data tool failed",
    "llm_failure": "language model failure (deterministic fallback used)",
    "web_search_failure": "external search unavailable",
    "guardrail_flag": "guardrail warning",
    "unsupported_numeric_claim": "answer contained numbers not supported by evidence",
    "insufficient_evidence": "evidence insufficient to answer fully",
    "explicit_human_request": "customer asked for a human agent",
    "high_risk_report": "potential fraud/security report",
    "approval_required": "proposed action requires human approval",
}


class PenaltyConfig(BaseModel):
    version: int = 1
    penalties: dict[str, float]
    force_escalation: list[str] = Field(default_factory=list)
    attack_signals: list[str] = Field(default_factory=list)

    @field_validator("penalties")
    @classmethod
    def _valid(cls, v: dict[str, float]) -> dict[str, float]:
        for k, p in v.items():
            if k not in KNOWN_SIGNALS:
                raise ValueError(f"unknown signal {k!r}")
            if not 0.0 <= p <= 1.0:
                raise ValueError(f"penalty for {k} must be within [0, 1]")
        return v


@lru_cache(maxsize=4)
def load_penalties(path: str) -> PenaltyConfig:
    return PenaltyConfig.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


@dataclass
class Decision:
    confidence: float
    needs_escalation: bool
    signals: list[str]
    reasons: list[str]
    priority: str
    interrupt_kind: str | None
    suppressed: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


_UNKNOWN_GET = re.compile(r"\bget ([a-z]+)( [a-z]+)?\b")
_KNOWN_GET_WORDS = {canonical(p).split()[1] for p in PRODUCTS if p.startswith("Get ")} | {
    "classic",
    "conta",
    "store",
    "pay",
    "ajuda",
    "checkout",
    "code",
    "renova",
    "net",
}


def entity_mismatch(message: str, evidence: Sequence[Mapping[str, Any]]) -> bool:
    text = canonical(message)
    for m in _UNKNOWN_GET.finditer(text):
        if m.group(1) not in _KNOWN_GET_WORDS:
            return True  # "Get Ultra Pro": a product name Getnet does not have
    product = mentioned_product(message)
    if not product or not evidence:
        return False
    key = canonical(product)
    return not any(
        key in canonical(" ".join(e.get("products") or [])) or key in canonical(e.get("text", "")) for e in evidence
    )


def collect_signals(
    state: AgentState, *, max_attempts: int, rerank_min: float, attempts: int
) -> tuple[list[str], dict[str, Any]]:
    signals: list[str] = list(state.get("signals") or [])
    detail: dict[str, Any] = {}
    intent = state.get("intent", "")
    flags = state.get("route_flags") or {}
    if intent == "human_support" or flags.get("wants_human"):
        signals.append("explicit_human_request")
    if intent == "unsupported":
        signals.append("unsupported_request")
    for f in state.get("guardrail_flags") or []:
        if f.get("rule") == "fraud_or_security_report":
            signals.append("high_risk_report")
        elif f.get("severity") == "warn":
            signals.append("guardrail_flag")
    answers = list(state.get("knowledge_answers") or [])
    getnet = [a for a in answers if a.get("mode") == "getnet"]
    # Evidence signals reflect the FINAL outcome across all knowledge steps (a broadened retry that found
    # evidence clears the empty filtered attempt). For intents where knowledge is not a core agent (e.g. a
    # sales lookup), a supervisor enrichment step that finds nothing must not penalize the answer.
    knowledge_core = intent in EVIDENCE_CORE_INTENTS
    if getnet and knowledge_core:
        answered = [a for a in getnet if a.get("status") in ("answered", "insufficient")]
        if not answered:
            signals.append("llm_failure" if all(a.get("status") == "error" for a in getnet) else "no_rag_evidence")
        else:
            top = max(float(a.get("top_rerank_score") or -99.0) for a in answered)
            if top < rerank_min + 1.0:
                signals.append("low_retrieval_score")
            if all(a.get("status") == "insufficient" for a in answered):
                signals.append("insufficient_evidence")
    for ka in answers:
        if ka.get("mode") == "web" and ka.get("status") in ("unavailable", "error"):
            signals.append("web_search_failure")
    if entity_mismatch(state.get("message", ""), list(state.get("retrieved_documents") or [])):
        signals.append("entity_mismatch")
    tools = state.get("tool_results") or []
    if any(t.get("status") == "error" for t in tools):
        signals.append("tool_failure")
    if intent in (
        "customer_settlement",
        "customer_transaction",
        "customer_terminal",
        "customer_account",
        "technical_support",
    ):
        findings = state.get("support_findings") or []
        if findings and all(f.get("status") == "failed" for f in findings):
            signals.append("customer_data_unavailable")
        if any(t.get("tool") == "get_customer_profile" and t.get("status") == "not_found" for t in tools):
            signals.append("customer_data_unavailable")
    if any(a.get("policy") == "requires_approval" for a in state.get("proposed_actions") or []):
        signals.append("approval_required")
    if family(intent) == "troubleshooting" and attempts >= max_attempts:
        signals.append("repeated_troubleshooting_failure")
        detail["attempts"] = attempts
    if settlement_contradiction(tools):
        signals.append("contradictory_information")
    if any("llm" in e or "provider" in e for e in state.get("errors") or []):
        signals.append("llm_failure")
    return list(dict.fromkeys(signals)), detail


def settlement_contradiction(tools: Sequence[Mapping[str, Any]]) -> bool:
    """Customer data contradicts the policy: a settlement still pending/scheduled after its expected date,
    or a credit settlement scheduled later than the merchant's contracted D+N."""
    for t in tools:
        if t.get("tool") != "get_settlement_schedule" or t.get("status") != "ok":
            continue
        data = t.get("data") or {}
        today = date.fromisoformat(str(data["today"])) if data.get("today") else date.today()
        credit_days = int((data.get("settlement_terms") or {}).get("credit_days") or 0)
        for s in data.get("settlements") or []:
            expected = date.fromisoformat(s["expected_date"])
            if s.get("status") in ("pending", "scheduled") and expected < today:
                return True
            if s.get("method") == "credit" and credit_days:
                sales = date.fromisoformat(s["sales_date"])
                if (expected - sales).days > credit_days + 4:  # +weekend/holiday tolerance
                    return True
    return False


def priority_for(signals: list[str], state: AgentState) -> str:
    if "high_risk_report" in signals:
        return "urgent"
    tools = state.get("tool_results") or []
    terminal_down = any(
        t.get("tool") == "get_terminal_status"
        and ((t.get("data") or {}).get("diagnostics") or {}).get("has_connectivity_issue")
        for t in tools
    )
    sales_today = None
    for t in tools:
        if t.get("tool") == "get_recent_transactions" and t.get("status") == "ok":
            period = (t.get("data") or {}).get("period") or {}
            if period.get("start") == period.get("end") and period.get("preset") == "today":
                sales_today = int(
                    ((t.get("data") or {}).get("totals_by_status") or {}).get("approved", {}).get("count", 0)
                )
    if terminal_down and (sales_today == 0 or "repeated_troubleshooting_failure" in signals):
        return "high"
    if state.get("intent") == "customer_settlement" and "contradictory_information" in signals:
        return "high"
    return "normal"


def sla_minutes(priority: str, base: int) -> int:
    return {"urgent": max(5, base // 2), "high": base, "normal": base * 4, "low": base * 8}.get(priority, base * 4)


def decide(
    state: AgentState, config: PenaltyConfig, *, threshold: float, max_attempts: int, rerank_min: float, attempts: int
) -> Decision:
    signals, detail = collect_signals(state, max_attempts=max_attempts, rerank_min=rerank_min, attempts=attempts)
    confidence = 1.0
    for s in signals:
        confidence -= config.penalties.get(s, 0.0)
    confidence = round(max(0.0, min(1.0, confidence)), 3)
    forced = [s for s in signals if s in config.force_escalation]
    attack = any(
        f.get("rule") in config.attack_signals and f.get("severity") == "block"
        for f in state.get("guardrail_flags") or []
    )
    needs = bool(forced) or confidence < threshold
    suppressed = None
    if needs and not forced:
        if attack:
            needs, suppressed = False, "suspected_attack_traffic"
        elif state.get("intent") == "unsupported":
            needs, suppressed = False, "unsupported_request"
        elif family(state.get("intent")) in ("knowledge", "external") and set(signals) <= EVIDENCE_SIGNALS | {
            "web_search_failure",
            "guardrail_flag",
            "llm_failure",
        }:
            # A knowledge gap is answered honestly ("no verified information") — no human case.
            needs, suppressed = False, "knowledge_gap_answered_honestly"
    reasons = []
    for s in signals:
        if s == "repeated_troubleshooting_failure":
            reasons.append(f"repeated troubleshooting failure ({detail.get('attempts', max_attempts)} attempts)")
        elif s in REASON_TEXT and (needs or s in forced):
            reasons.append(REASON_TEXT[s])
    if needs and not reasons:
        reasons.append(f"low confidence ({confidence})")
    return Decision(
        confidence=confidence,
        needs_escalation=needs,
        signals=signals,
        reasons=reasons if needs else [],
        priority=priority_for(signals, state) if needs else "normal",
        interrupt_kind="approval" if needs and "approval_required" in signals else ("reply" if needs else None),
        suppressed=suppressed,
        detail=detail,
    )
