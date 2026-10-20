"""Deterministic handlers for ``FakeLLMProvider`` (TEST DOUBLE — refused in production).

They emulate each agent's contract with simple, explicit heuristics so the *deterministic* parts of the
system (graph, supervisor, tools, retrieval, guardrails, decision model, memory, HITL) can be tested and
evaluated offline without MiNiMax credits. Their "language understanding" is intentionally shallow;
LLM quality is measured separately by the live evaluation against MiniMax-M3.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.guardrails.normalize import canonical
from app.guardrails.output import detect_language
from app.llm.base import LLMResponse, Message
from app.llm.fake import task_input, tool_call_response

Handler = Any

_EVIDENCE = re.compile(
    r"\[(E\d+)\] title: (.*?) \| url: (.*?) \| category: (.*?)\n(.*?)(?=\n\n\[E\d+\] title:|\n</retrieved_evidence>)",
    re.S,
)
_WEB = re.compile(r"\[(W\d+)\] (.*?) \| (.*?) \| retrieved_at: (.*?)\n(.*?)(?=\n\n\[W\d+\] |\n</web_results>)", re.S)

_INTENT_PATTERNS: list[tuple[str, str]] = [
    ("human_support", r"\b(humano|atendente|human|agent|pessoa real)"),
    ("weather", r"\b(previsao|clima|chuva|weather|forecast|temperatura)"),
    ("exchange_rate", r"\b(cotacao|cambio|exchange rate|dolar|euro)"),
    ("case_status", r"\b(chamado|protocolo|ticket|case)"),
    (
        "customer_settlement",
        r"\b(deposit|depositad|cai |cair|repasse|recebo|receber o|recebimento das|liquida|settle|paid out|money from)",
    ),
    (
        "technical_support",
        r"\b(nao conecta|nao liga|conect|conex|internet|wi-?fi|sem sinal|travou|erro|error|won'?t connect|not connect|reinici|restart|broken|quebrad|troca|trocar|substitu|replace)",
    ),
    (
        "customer_transaction",
        r"\b(minhas vendas|my sales|vendas de|vendi|sales from|minhas transac|negad|recusad|declin|anteontem|day before|ontem|yesterday)",
    ),
    (
        "customer_terminal",
        r"\b(minhas maquininhas|maquininhas eu tenho|my (card )?machines|machines do i have|meus terminais)",
    ),
    ("customer_account", r"\b(meu plano|my plan|meus dados|domicilio|minha conta bancaria)"),
    ("unsupported", r"\b(poema|poem|receita|recipe|piada|joke|futebol|football)"),
]
_PRODUCTS = ["Get Smart", "Get Clássica", "Get Mini", "Get Tap", "Get Lite", "Link de Pagamento", "Pix", "Crediário"]
_CUSTOMER = {
    "technical_support",
    "customer_settlement",
    "customer_transaction",
    "customer_terminal",
    "customer_account",
    "case_status",
}


def _date_ref(text: str) -> str | None:
    if re.search(r"anteontem|day before yesterday", text):
        return "day_before_yesterday"
    if re.search(r"ontem|yesterday", text):
        return "yesterday"
    if re.search(r"hoje|today", text):
        return "today"
    if re.search(r"semana|week", text):
        return "last_7_days"
    return None


_EN_PT = [
    (r"connect|internet|wi-?fi|offline|signal", "maquininha não conecta internet wi-fi chip conexão"),
    (r"installment", "parcelas parcelamento"),
    (r"advance|antecipa", "antecipação de vendas recebíveis"),
    (r"bank account", "conta bancária chave pix domicílio"),
    (r"payment link|whatsapp", "link de pagamento whatsapp enviar"),
    (r"declin|decline", "venda negada não autorizada emissor"),
    (r"deposit|settle|receive", "prazo de recebimento repasse"),
    (r"difference|compare", "diferença comparação"),
    (r"battery", "bateria duração"),
    (r"refund", "estorno venda"),
    (r"fee", "taxas tarifa"),
]


def _pt_query(msg: str, text: str) -> str:
    """Emulates the router's standalone Portuguese query rewrite (Getnet content is mainly PT)."""
    extra = " ".join(pt for pat, pt in _EN_PT if re.search(pat, text))
    return f"{msg} {extra}".strip()


def router(messages: list[Message], tools: list[dict[str, Any]] | None) -> LLMResponse:
    ti = task_input(messages)
    msg = ti.get("message", "")
    text = canonical(msg)
    slots = ti.get("slots") or {}
    intent = next((i for i, p in _INTENT_PATTERNS if re.search(p, text)), None)
    # Product/policy questions ("Do I need a bank account to receive via Pix?") are knowledge questions
    # unless they ask about the merchant's own dated data.
    product_q = re.search(
        r"\b(pix|link de pagamento|payment link|crediario|antecipa|domicilio|get (smart|classica|mini|tap|lite))", text
    ) and re.search(r"\b(preciso|need|como|how|posso|can|o que|what|qual|quais|which|funciona|work)", text)
    own_dated = re.search(
        r"\b(ontem|anteontem|hoje|yesterday|today|minhas vendas de|my sales (from|of|yesterday))", text
    )
    own_problem = re.search(
        r"\b(minha|meu|my) (maquininha|get|card machine|machine|terminal)|nao conecta|won'?t connect|sem conexao", text
    )
    if (
        product_q
        and not own_dated
        and not own_problem
        and intent in ("customer_transaction", "customer_settlement", "technical_support", None)
    ):
        intent = "getnet_product_information"
    if intent == "customer_transaction" and re.search(r"deposit|cai|repasse|receb", text):
        intent = "customer_settlement"
    if intent == "technical_support" and re.search(r"negad|recusad|declin", text):
        intent = "customer_transaction"
    if intent is None and len(text.split()) <= 7 and slots.get("last_intent") in _CUSTOMER:
        intent = slots["last_intent"]  # follow-up ("e a de anteontem?", "já reiniciei e continua")
    if intent is None:
        intent = "getnet_product_information"
    lang = detect_language(msg) or slots.get("preferred_language") or "pt"
    product = next((p for p in _PRODUCTS if canonical(p) in text), None)
    needs_k = intent in ("technical_support", "customer_settlement") or intent.startswith("getnet_")
    plan: list[dict[str, Any]] = []
    if intent in _CUSTOMER:
        plan.append({"agent": "customer_support", "objective": f"retrieve data for {intent}", "depends_on": []})
    if needs_k:
        plan.append(
            {
                "agent": "knowledge",
                "objective": "official Getnet information",
                "depends_on": [0] if intent == "technical_support" else [],
            }
        )
    if intent in ("weather", "exchange_rate", "general_information"):
        plan.append({"agent": "knowledge", "objective": "current external information", "depends_on": []})
    out = {
        "intent": intent,
        "requires_customer_data": intent in _CUSTOMER,
        "requires_getnet_knowledge": needs_k,
        "requires_external_web": intent in ("weather", "exchange_rate"),
        "wants_human": intent == "human_support",
        "language": lang,
        "knowledge_query": _pt_query(msg, text) if lang == "en" else msg,
        "product": product,
        "date_reference": _date_ref(text)
        or (
            (slots.get("last_date_range") or {}).get("preset")
            if intent in ("customer_transaction", "customer_settlement")
            else None
        ),
        "plan": plan,
    }
    return LLMResponse(content=json.dumps(out))


def customer_support(messages: list[Message], tools: list[dict[str, Any]] | None) -> LLMResponse:
    ti = task_input(messages)
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    called = [
        tc["function"]["name"] for m in messages if m.get("role") == "assistant" for tc in m.get("tool_calls", [])
    ]
    intent = ti.get("intent", "")
    text = canonical(ti.get("message", ""))
    preset = ti.get("date_reference") or _date_ref(text)
    if not called:
        if intent == "customer_settlement":
            return tool_call_response(
                ("get_settlement_schedule", {"by": "sales_date", "date_preset": preset or "yesterday"}),
                ("get_recent_transactions", {"date_preset": preset or "yesterday"}),
            )
        if intent == "customer_transaction":
            args: dict[str, Any] = {"date_preset": preset or "last_7_days"}
            if re.search(r"negad|recusad|declin", text):
                args["status"] = "declined"
            return tool_call_response(("get_recent_transactions", args))
        if intent in ("technical_support", "customer_terminal"):
            return tool_call_response(("get_customer_terminals", {}))
        if intent == "case_status":
            return tool_call_response(("get_open_support_cases", {}))
        if intent == "customer_account":
            return tool_call_response(("get_customer_profile", {}))
    if called and called[-1] == "get_customer_terminals" and tool_msgs:
        try:
            terms = json.loads(tool_msgs[-1]["content"]).get("terminals", [])
        except (json.JSONDecodeError, AttributeError):
            terms = []
        calls = [("get_terminal_status", {"terminal_id": t["terminal_id"]}) for t in terms[:3]]
        if re.search(r"troca|substitu|replace|nova maquininha|new machine", text) and terms:
            calls.append(
                (
                    "request_terminal_replacement",
                    {
                        "terminal_id": terms[0]["terminal_id"],
                        "reason": "merchant reports persistent fault; diagnostics attached",
                    },
                )
            )
        if calls:
            return tool_call_response(*calls)
    summary = " | ".join(str(m.get("content", ""))[:300] for m in tool_msgs) or "no data"
    return LLMResponse(content=f"Findings: {summary}")


def parse_evidence(messages: list[Message]) -> list[dict[str, str]]:
    content = messages[-1].get("content", "") if messages else ""
    return [
        {"id": m[0], "title": m[1], "url": m[2], "category": m[3], "text": m[4]} for m in _EVIDENCE.findall(content)
    ]


def knowledge_rag(messages: list[Message], tools: list[dict[str, Any]] | None) -> LLMResponse:
    ev = parse_evidence(messages)
    if not ev:
        return LLMResponse(
            content=json.dumps({"answer": "No verified information found.", "used_evidence": [], "insufficient": True})
        )
    used = ev[:2]
    parts = []
    for e in used:
        body = e["text"].split("\n", 1)[-1].strip().replace("\n", " ")
        parts.append(f"{body[:420]} [{e['id']}]")
    return LLMResponse(
        content=json.dumps({"answer": " ".join(parts), "used_evidence": [e["id"] for e in used], "insufficient": False})
    )


def knowledge_web(messages: list[Message], tools: list[dict[str, Any]] | None) -> LLMResponse:
    content = messages[-1].get("content", "") if messages else ""
    results = _WEB.findall(content)
    if not results:
        return LLMResponse(
            content=json.dumps({"answer": "Information not available.", "used_results": [], "insufficient": True})
        )
    rid, title, url, at, text = results[0]
    return LLMResponse(
        content=json.dumps(
            {
                "answer": f"{text.strip()[:300]} (source: {title}, {url}, retrieved {at[:16]})",
                "used_results": [rid],
                "insufficient": False,
            }
        )
    )


def synthesizer(messages: list[Message], tools: list[dict[str, Any]] | None) -> LLMResponse:
    ti = task_input(messages)
    parts = []
    if ti.get("customer_data_facts"):
        parts.append(ti["customer_data_facts"])
    for a in ti.get("knowledge_answers") or []:
        if a.get("answer"):
            parts.append(a["answer"])
    if ti.get("proposed_actions"):
        parts.append(
            "Replacement requested; awaiting approval."
            if ti.get("language") == "en"
            else "Solicitação de troca registrada; aguardando aprovação."
        )
    return LLMResponse(content="\n\n".join(parts) or "OK")


def escalation(messages: list[Message], tools: list[dict[str, Any]] | None) -> LLMResponse:
    ti = task_input(messages)
    ctx = ti.get("context") or {}
    intent = ctx.get("intent") or ""
    category = {
        "technical_support": "POS_CONNECTIVITY",
        "customer_terminal": "POS_HARDWARE",
        "customer_transaction": "TRANSACTION_ISSUE",
        "customer_settlement": "SETTLEMENT_ISSUE",
        "customer_account": "ACCOUNT_ISSUE",
    }.get(intent, "PRODUCT_QUESTION" if intent.startswith("getnet_") else "GENERAL_REQUEST")
    reasons = ", ".join(ti.get("reasons") or [])
    return LLMResponse(
        content=json.dumps(
            {
                "category": category,
                "subject": f"Atendimento: {intent or 'geral'}"[:80],
                "summary": f"Motivos: {reasons}. Mensagem: {ctx.get('customer_message', '')[:200]}",
            }
        )
    )


def human_reply(messages: list[Message], tools: list[dict[str, Any]] | None) -> LLMResponse:
    ti = task_input(messages)
    lead = (
        "Message from the Getnet support team:"
        if ti.get("language") == "en"
        else "Mensagem da equipe de suporte Getnet:"
    )
    extra = ""
    result = ti.get("action_result") or {}
    if (result.get("data") or {}).get("service_order_id"):
        extra = f" (ordem de serviço {result['data']['service_order_id']})"
    return LLMResponse(content=f"{lead} {ti.get('operator_message', '')}{extra}")


def summary(messages: list[Message], tools: list[dict[str, Any]] | None) -> LLMResponse:
    ti = task_input(messages)
    turns = ti.get("turns") or []
    topics = list(dict.fromkeys(t["content"][:40] for t in turns if t.get("role") == "customer"))[:5]
    return LLMResponse(content=json.dumps({"topics": topics, "open_issues": [], "entities": {}, "pending_actions": []}))


def supervisor(messages: list[Message], tools: list[dict[str, Any]] | None) -> LLMResponse:
    return LLMResponse(content=json.dumps({"decision": "synthesize", "reason": "fake supervisor: findings sufficient"}))


def judge(messages: list[Message], tools: list[dict[str, Any]] | None) -> LLMResponse:
    return LLMResponse(content=json.dumps({"grounded": True, "complete": True, "reason": "fake"}))


DEFAULT_HANDLERS: dict[str, Handler] = {
    "router": router,
    "customer_support": customer_support,
    "knowledge_rag": knowledge_rag,
    "knowledge_web": knowledge_web,
    "synthesizer": synthesizer,
    "escalation": escalation,
    "human_reply": human_reply,
    "summary": summary,
    "supervisor": supervisor,
    "judge": judge,
}
