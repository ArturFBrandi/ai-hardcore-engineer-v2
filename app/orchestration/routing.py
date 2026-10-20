"""Hybrid routing: deterministic high-confidence rules first, LLM classification otherwise, deterministic
fallback when the LLM fails. The final agent list is ALWAYS derived by code from the intent and flags —
the LLM classifies, code disposes.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.guardrails.normalize import canonical
from app.guardrails.output import detect_language
from app.orchestration.state import INTENTS

PRODUCTS = ("Get Smart", "Get Clássica", "Get Mini", "Get Lite", "Get Tap", "Link de Pagamento", "Pix", "Crediário")

# intent → specialists that may work on it (code-owned), and which run first.
INTENT_AGENTS: dict[str, list[str]] = {
    "getnet_product_information": ["knowledge"],
    "getnet_policy": ["knowledge"],
    "getnet_how_to": ["knowledge"],
    "technical_support": ["customer_support", "knowledge"],
    "customer_settlement": ["customer_support", "knowledge"],
    "customer_transaction": ["customer_support"],
    "customer_terminal": ["customer_support"],
    "customer_account": ["customer_support"],
    "case_status": ["customer_support"],
    "weather": ["knowledge"],
    "exchange_rate": ["knowledge"],
    "general_information": ["knowledge"],
    "human_support": [],
    "unsupported": [],
}
WEB_INTENTS = {"weather", "exchange_rate", "general_information"}
CUSTOMER_INTENTS = {
    "technical_support",
    "customer_settlement",
    "customer_transaction",
    "customer_terminal",
    "customer_account",
    "case_status",
}
# Agents that may be ADDED by flags for each intent (allowed transitions).
OPTIONAL_AGENTS: dict[str, set[str]] = {
    "customer_transaction": {"knowledge"},
    "customer_terminal": {"knowledge"},
    "customer_account": {"knowledge"},
    "case_status": set(),
    "getnet_product_information": set(),
    "getnet_policy": set(),
    "getnet_how_to": set(),
}


class PlanProposal(BaseModel):
    agent: str
    objective: str = Field(default="", max_length=300)
    depends_on: list[int] = Field(default_factory=list)


class RouterOutput(BaseModel):
    intent: str
    requires_customer_data: bool = False
    requires_getnet_knowledge: bool = False
    requires_external_web: bool = False
    wants_human: bool = False
    language: Literal["pt", "en"] = "pt"
    knowledge_query: str | None = Field(default=None, max_length=400)
    product: str | None = None
    date_reference: str | None = None
    plan: list[PlanProposal] = Field(default_factory=list, max_length=6)

    @field_validator("intent")
    @classmethod
    def _known_intent(cls, v: str) -> str:
        if v not in INTENTS:
            raise ValueError(f"unknown intent {v!r}")
        return v

    @field_validator("product")
    @classmethod
    def _known_product(cls, v: str | None) -> str | None:
        if v is None:
            return None
        match = next((p for p in PRODUCTS if canonical(p) == canonical(v)), None)
        return match  # unknown product names are dropped (entity mismatch is handled by the decision model)


class RuleMatch(BaseModel):
    intent: str
    flags: dict[str, Any]
    rule: str


_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    (
        "human_support",
        "explicit_human",
        re.compile(
            r"\b(falar|conversar|atendimento) (com )?(um |uma )?(atendente|humano|pessoa|operador|gerente|alguem)|"
            r"\b(talk|speak|chat) (to|with) (a |an )?(human|person|agent|operator|someone|representative)|\bhuman agent\b|\breal person\b|\batendimento humano\b"
        ),
    ),
    (
        "weather",
        "weather",
        re.compile(
            r"\b(previsao do tempo|tempo (amanha|hoje)|vai chover|chuva|temperatura|clima em|weather|forecast|rain|temperature)\b"
        ),
    ),
    (
        "exchange_rate",
        "exchange_rate",
        re.compile(
            r"\b(cotacao|cambio|exchange rate|taxa de cambio|quanto (esta|ta) o (dolar|euro)|(dolar|euro|usd|eur|libra|peso)( hoje| today| rate| agora)|"
            r"(dollar|euro|pound) (rate|today|price))\b"
        ),
    ),
    (
        "case_status",
        "case_status",
        re.compile(
            r"\b(status|andamento|situacao|novidade)\b.{0,30}\b(chamado|protocolo|caso|ticket|solicitacao|atendimento)\b|"
            r"\b(my|meu) (case|ticket|chamado|protocolo)\b|\bcase-\d{8}-\d{4}\b"
        ),
    ),
]


# Messages with nothing to act on (a greeting or a bare "help"): ask what the merchant needs instead of
# escalating, searching the web for "hello" or guessing an intent.
_VAGUE = re.compile(
    r"^(oi+|ola|ola tudo bem|bom dia|boa tarde|boa noite|ajuda|socorro|help|hello|hi|hey|"
    r"preciso de ajuda|pode me ajudar|me ajuda|tenho uma duvida|i need help|can you help( me)?)[\s!?.,]*$"
)


def is_vague(message: str) -> bool:
    return bool(_VAGUE.match(canonical(message)))


def rule_route(message: str) -> RuleMatch | None:
    text = canonical(message)
    if is_vague(message):
        flags = {
            "requires_customer_data": False,
            "requires_getnet_knowledge": False,
            "requires_external_web": False,
            "wants_human": False,
        }
        return RuleMatch(intent="unsupported", flags=flags, rule="too_vague")
    for intent, rule, pattern in _RULES:
        if pattern.search(text):
            flags = {
                "requires_customer_data": intent in CUSTOMER_INTENTS,
                "requires_getnet_knowledge": False,
                "requires_external_web": intent in WEB_INTENTS,
                "wants_human": intent == "human_support",
            }
            return RuleMatch(intent=intent, flags=flags, rule=rule)
    return None


# Deterministic fallback classifier used only when the LLM is unavailable/invalid. Deliberately small.
_FALLBACK: list[tuple[str, re.Pattern[str]]] = [
    (
        "customer_settlement",
        re.compile(
            r"\b(deposit\w*|cai (o|na)|cair|repasse\w*|recebo|receber|recebimento\w*|settle\w*|paid out|dinheiro das vendas|liquidac\w*)"
        ),
    ),
    (
        "technical_support",
        re.compile(
            r"\b(nao conecta|sem conexao|wi-?fi|internet|erro\w*|error\w*|nao liga|travad\w*|quebrad\w*|won'?t connect|not connect\w*|offline|broken|reinici\w*|restart\w*)"
        ),
    ),
    (
        "customer_transaction",
        re.compile(
            r"\b(minhas vendas|my sales|transac\w*|venda\w* (de )?(ontem|hoje)|negad\w*|recusad\w*|declin\w*|estorn\w*|refund\w*|anteontem|ontem|yesterday)"
        ),
    ),
    ("customer_terminal", re.compile(r"\b(minhas? maquininhas?|my (card )?machines?|meus? termina\w*|my terminals?)")),
    ("customer_account", re.compile(r"\b(meu plano|my plan|meus dados|my account|minha conta|domicilio)")),
]


def fallback_route(message: str, slots: dict[str, Any] | None = None) -> RouterOutput:
    text = canonical(message)
    language = detect_lang(message, (slots or {}).get("preferred_language"))
    intent = next((i for i, p in _FALLBACK if p.search(text)), None)
    if intent is None:
        last = (slots or {}).get("last_intent")
        is_follow_up = len(text.split()) <= 6 and last in CUSTOMER_INTENTS
        intent = last if is_follow_up else "getnet_product_information"
    return RouterOutput(
        intent=intent,
        requires_customer_data=intent in CUSTOMER_INTENTS,
        requires_getnet_knowledge=intent in ("technical_support", "customer_settlement", "getnet_product_information"),
        language=language,
        knowledge_query=message,
    )


def detect_lang(message: str, previous: str | None = None) -> str:
    lang = detect_language(message)
    if lang is None or (len(message.split()) <= 3 and previous):
        return previous or lang or "pt"
    return lang


def derive_agents(intent: str, flags: dict[str, Any]) -> list[str]:
    """Code decides the specialist list from the classified intent + flags (allowed transitions only)."""
    agents = list(INTENT_AGENTS.get(intent, []))
    optional = OPTIONAL_AGENTS.get(intent, set())
    if flags.get("requires_getnet_knowledge") and "knowledge" in optional and "knowledge" not in agents:
        agents.append("knowledge")
    return agents


def mentioned_product(message: str) -> str | None:
    text = canonical(message)
    for p in PRODUCTS:
        if canonical(p) in text:
            return p
    if re.search(r"\bget ?classic", text):
        return "Get Clássica"
    return None
