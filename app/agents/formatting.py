"""Deterministic, language-aware rendering of tool data and canned messages.

Used (a) as compact, grounded input for the Synthesizer, (b) as the deterministic fallback answer when
the LLM is unavailable, and (c) for handoff/unavailable messages that must never depend on the LLM.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.orchestration.state import ToolResultEntry


def brl(value: str | float | Decimal | None, lang: str) -> str:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return str(value)
    s = f"{d:,.2f}"
    if lang == "pt":
        return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")
    return "BRL " + s


def fmt_date(value: str | None, lang: str) -> str:
    if not value:
        return "-"
    try:
        d = date.fromisoformat(value[:10])
    except ValueError:
        return value
    return d.strftime("%d/%m/%Y") if lang == "pt" else d.isoformat()


_STATUS = {
    "pt": {
        "completed": "pago",
        "pending": "pendente (previsto para hoje)",
        "scheduled": "agendado",
        "approved": "aprovada",
        "declined": "negada",
        "refunded": "estornada",
        "active": "ativa",
        "inactive": "inativa",
        "maintenance": "em manutenção",
        "blocked": "bloqueada",
        "open": "aberto",
        "in_progress": "em andamento",
        "awaiting_customer": "aguardando você",
        "resolved": "resolvido",
        "closed": "encerrado",
    },
    "en": {
        "completed": "paid",
        "pending": "pending (due today)",
        "scheduled": "scheduled",
        "approved": "approved",
        "declined": "declined",
        "refunded": "refunded",
        "active": "active",
        "inactive": "inactive",
        "maintenance": "under maintenance",
        "blocked": "blocked",
        "open": "open",
        "in_progress": "in progress",
        "awaiting_customer": "awaiting your reply",
        "resolved": "resolved",
        "closed": "closed",
    },
}
_METHOD = {
    "pt": {
        "credit": "crédito",
        "debit": "débito",
        "pix": "Pix",
        "payment_link": "link de pagamento",
        "crediario": "crediário",
    },
    "en": {
        "credit": "credit",
        "debit": "debit",
        "pix": "Pix",
        "payment_link": "payment link",
        "crediario": "crediário",
    },
}


def _st(v: str, lang: str) -> str:
    return _STATUS[lang].get(v, v)


def _m(v: str, lang: str) -> str:
    return _METHOD[lang].get(v, v)


def _d_get_settlement_schedule(data: dict[str, Any], lang: str) -> str:
    rows = data.get("settlements") or []
    if not rows:
        return "Não há repasses no período consultado." if lang == "pt" else "There are no settlements in that period."
    head = "Repasses das vendas:" if lang == "pt" else "Settlements for those sales:"
    lines = [head]
    for s in rows:
        if lang == "pt":
            lines.append(
                f"- Vendas de {fmt_date(s['sales_date'], lang)} no {_m(s['method'], lang)}: líquido {brl(s['net_amount'], lang)} "
                f"(bruto {brl(s['gross_amount'], lang)}, taxas {brl(s['fees'], lang)}), previsto para {fmt_date(s['expected_date'], lang)} — {_st(s['status'], lang)}."
            )
        else:
            lines.append(
                f"- {_m(s['method'], lang).capitalize()} sales of {fmt_date(s['sales_date'], lang)}: net {brl(s['net_amount'], lang)} "
                f"(gross {brl(s['gross_amount'], lang)}, fees {brl(s['fees'], lang)}), expected on {fmt_date(s['expected_date'], lang)} — {_st(s['status'], lang)}."
            )
    return "\n".join(lines)


def _d_get_recent_transactions(data: dict[str, Any], lang: str) -> str:
    rows = data.get("transactions") or []
    period = data.get("period") or {}
    when = (
        fmt_date(period.get("start"), lang)
        if period.get("start") == period.get("end")
        else f"{fmt_date(period.get('start'), lang)}–{fmt_date(period.get('end'), lang)}"
    )
    if not rows:
        return f"Nenhuma venda encontrada em {when}." if lang == "pt" else f"No sales found for {when}."
    approved = [t for t in rows if t["status"] == "approved"]
    total = sum(Decimal(t["amount"]) for t in approved)
    head = (
        f"Vendas em {when}: {len(rows)} transações, {len(approved)} aprovadas somando {brl(total, lang)}."
        if lang == "pt"
        else f"Sales on {when}: {len(rows)} transactions, {len(approved)} approved totalling {brl(total, lang)}."
    )
    lines = [head]
    for t in rows[:8]:
        extra = f" — {t['decline_code']}: {t['decline_reason']}" if t.get("decline_code") else ""
        lines.append(
            f"- {t['transaction_id']} {brl(t['amount'], lang)} {_m(t['method'], lang)} {_st(t['status'], lang)}{extra}"
        )
    return "\n".join(lines)


def _d_get_transaction_details(data: dict[str, Any], lang: str) -> str:
    extra = f" — {data.get('decline_code')}: {data.get('decline_reason')}" if data.get("decline_code") else ""
    return f"{data.get('transaction_id')} {brl(data.get('amount'), lang)} {_m(str(data.get('method')), lang)} {_st(str(data.get('status')), lang)}{extra}"


def _d_get_customer_terminals(data: dict[str, Any], lang: str) -> str:
    terms = data.get("terminals") or []
    label = "Suas maquininhas" if lang == "pt" else "Your card machines"
    return (
        label
        + ": "
        + "; ".join(f"{t['model']} (#{t['terminal_id']}, {_st(t['status'], lang)}, {t['connectivity']})" for t in terms)
    )


def _d_get_terminal_status(data: dict[str, Any], lang: str) -> str:
    diag = data.get("diagnostics") or {}
    n = diag.get("connectivity_losses_24h", 0)
    if lang == "pt":
        msg = f"{data.get('model')} (#{data.get('terminal_id')}): {_st(str(data.get('status')), lang)}, conexão {data.get('connectivity')}"
        if n:
            msg += f"; {n} quedas de conexão nas últimas 24h"
    else:
        msg = f"{data.get('model')} (#{data.get('terminal_id')}): {_st(str(data.get('status')), lang)}, connectivity {data.get('connectivity')}"
        if n:
            msg += f"; {n} connectivity losses in the last 24h"
    if diag.get("recent_error_codes"):
        msg += f"; {'códigos de erro' if lang == 'pt' else 'error codes'}: {', '.join(diag['recent_error_codes'])}"
    return msg + "."


def _d_get_open_support_cases(data: dict[str, Any], lang: str) -> str:
    cases = data.get("cases") or []
    if not cases:
        return "Você não tem chamados abertos." if lang == "pt" else "You have no open support cases."
    label = "Chamados abertos" if lang == "pt" else "Open support cases"
    return label + ": " + "; ".join(f"{c['case_id']} — {c['subject']} ({_st(c['status'], lang)})" for c in cases)


def _d_get_customer_profile(data: dict[str, Any], lang: str) -> str:
    if lang == "pt":
        return (
            f"Plano {data.get('plan')}; crédito recebido em D+{data.get('credit_settlement_days')}; "
            f"Pix {'habilitado' if data.get('pix_enabled') else 'não habilitado'}; antecipação {'habilitada' if data.get('anticipation_enabled') else 'não habilitada'}."
        )
    return (
        f"Plan {data.get('plan')}; credit settled in D+{data.get('credit_settlement_days')}; "
        f"Pix {'enabled' if data.get('pix_enabled') else 'not enabled'}; anticipation {'enabled' if data.get('anticipation_enabled') else 'not enabled'}."
    )


_RENDERERS: dict[str, Any] = {
    "get_settlement_schedule": _d_get_settlement_schedule,
    "get_recent_transactions": _d_get_recent_transactions,
    "get_transaction_details": _d_get_transaction_details,
    "get_customer_terminals": _d_get_customer_terminals,
    "get_terminal_status": _d_get_terminal_status,
    "get_open_support_cases": _d_get_open_support_cases,
    "get_customer_profile": _d_get_customer_profile,
}


def describe_tool(result: ToolResultEntry, lang: str) -> str:
    lang = "pt" if lang == "pt" else "en"
    if result.get("status") == "not_found":
        return (
            "Não encontrei esse registro na sua conta."
            if lang == "pt"
            else "I could not find that record in your account."
        )
    if result.get("status") != "ok":
        return ""
    renderer = _RENDERERS.get(str(result.get("tool")))
    return renderer(result.get("data") or {}, lang) if renderer else ""


def describe_all(results: list[ToolResultEntry], lang: str) -> str:
    seen: set[str] = set()
    parts = []
    for r in results:
        text = describe_tool(r, lang)
        if text and text not in seen:
            seen.add(text)
            parts.append(text)
    return "\n".join(parts)


MESSAGES: dict[str, dict[str, str]] = {
    "no_evidence": {
        "pt": "Não encontrei informações verificadas nas fontes oficiais da Getnet para responder isso com segurança. Você pode confirmar pelos canais oficiais (Central de Atendimento) ou pedir para falar com um atendente.",
        "en": "I couldn't find verified information in Getnet's official sources to answer this reliably. You can confirm through Getnet's official channels (customer service) or ask to talk to a human agent.",
    },
    "web_unavailable": {
        "pt": "No momento não consigo consultar informações externas atualizadas. Tente novamente em alguns minutos.",
        "en": "I cannot retrieve current external information right now. Please try again in a few minutes.",
    },
    "kb_unavailable": {
        "pt": "A base de conhecimento oficial está indisponível no momento, então não vou responder de memória. Tente novamente em instantes.",
        "en": "The official knowledge base is unavailable right now, so I won't answer from memory. Please try again shortly.",
    },
    "unsupported": {
        "pt": "Posso ajudar com dúvidas sobre produtos e serviços Getnet, sua conta, vendas, repasses e maquininhas — e com informações gerais como clima e câmbio. Esse pedido está fora do que posso atender.",
        "en": "I can help with Getnet products and services, your account, sales, settlements and card machines — and general information like weather and exchange rates. That request is outside what I can help with.",
    },
    "handoff": {
        "pt": "Encaminhei seu atendimento para um especialista da Getnet. Protocolo: {case_id} (prioridade {priority}). A resposta aparecerá aqui nesta conversa.",
        "en": "I've passed your request to a Getnet specialist. Case: {case_id} (priority {priority}). The reply will appear here in this conversation.",
    },
    "handoff_approval": {
        "pt": "Registrei a solicitação de {action} para a sua maquininha. Ela precisa da aprovação de um especialista da Getnet — protocolo {case_id}. Você será avisado aqui assim que houver uma decisão.",
        "en": "I've registered a {action} request for your card machine. It needs approval from a Getnet specialist — case {case_id}. You'll be notified here as soon as there's a decision.",
    },
    "handoff_failed": {
        "pt": "Não consegui registrar seu chamado agora. Por favor, fale com a Central de Relacionamento Getnet: 4002-4000 / 4003-4000 (capitais e regiões metropolitanas) ou 0800-648-8000 (demais localidades).",
        "en": "I couldn't register your case right now. Please contact Getnet customer service: 4002-4000 / 4003-4000 (capitals and metro areas) or 0800-648-8000 (other locations).",
    },
    "repeated_failure": {
        "pt": "Como as orientações anteriores não resolveram, vou envolver um especialista.",
        "en": "Since the previous steps didn't solve it, I'm bringing in a specialist.",
    },
    "safe_block": {
        "pt": "Não posso enviar essa resposta. Posso ajudar com outra dúvida sobre a Getnet?",
        "en": "I can't send that response. Can I help with another Getnet question?",
    },
}

ACTION_LABEL = {"request_terminal_replacement": {"pt": "troca", "en": "replacement"}}
PRIORITY_LABEL = {"pt": {"urgent": "urgente", "high": "alta", "normal": "normal", "low": "baixa"}, "en": {}}


def message(key: str, lang: str, **kw: str) -> str:
    template = MESSAGES[key]["pt" if lang == "pt" else "en"]
    if "priority" in kw and lang == "pt":
        kw["priority"] = PRIORITY_LABEL["pt"].get(kw["priority"], kw["priority"])
    return template.format(**kw)


def value_dump(data: Any, limit: int = 3000) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)[:limit]
