"""Input guardrails (deterministic, layered).

Rules run on every normalized view of the message (see ``normalize.views``) and, for multi-turn
injection, on the recent customer turns. Each finding is a ``GuardrailFlag`` with a severity:

* ``block`` — the request is answered with a safe refusal, no specialist runs (prompt-injection /
  system-prompt extraction, cross-customer data access, bulk data dumps, credential harvesting).
* ``warn``  — processing continues but the decision model is informed (e.g. sensitive data shared).
* ``info``  — routing hints (fraud/security report → high-risk escalation, not a refusal).

LLM prompts are additionally hardened (untrusted-data delimiters, canary token), so the detectors are a
first layer, not the only one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.guardrails.normalize import canonical, views
from app.guardrails.redaction import contains_sensitive
from app.observability import metrics


@dataclass(frozen=True)
class Rule:
    name: str
    severity: str
    patterns: tuple[str, ...]

    def matches(self, text: str) -> bool:
        return any(re.search(p, text) for p in self.patterns)


INJECTION = Rule(
    "prompt_injection",
    "block",
    (
        r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(previous|prior|above|all|your|the|system)\b.{0,30}\b(instruction|rule|prompt|polic|guideline|direction)",
        r"\b(ignor[ea]|esque[cç]a|desconsider[ea]|desobede[cç]a)\b.{0,40}\b(instru[cç]|regra|prompt|orienta|diretriz)",
        r"\byou are now\b|\bfrom now on you\b|\bact as (an? )?(unrestricted|jailbroken|dan|developer mode)",
        r"\b(a partir de agora|agora) voce (e|sera)\b.{0,40}\b(sem (restri|regra)|livre|hacker|dan)\b",
        r"\bdeveloper mode\b|\bjailbreak\b|\bmodo (desenvolvedor|deus|sem restri)",
        r"</?(system|assistant|retrieved_evidence|conversation_history|task_input)\b",
        r"\[(system|inst)\]|<<sys>>|###\s*(system|instruction)",
    ),
)
PROMPT_EXTRACTION = Rule(
    "system_prompt_extraction",
    "block",
    (
        r"\b(reveal|show|print|repeat|display|output|tell me|leak|dump)\b.{0,40}\b(system|hidden|initial|original|internal)\b.{0,20}\b(prompt|instruction|message|rule)",
        r"\b(mostre|revele|imprima|repita|exiba|diga|mostrar|revelar)\b.{0,40}\b(prompt|instru[cç][oõ]es)\b.{0,30}\b(sistema|iniciai?s|internas?|ocultas?|originais?)",
        r"\bwhat (is|are|were) your (system |initial |original )?(prompt|instructions)\b",
        r"\bquais? (sao|e) (as )?suas? instru[cç]",
        r"\bcanary\b|\bchain[- ]of[- ]thought\b|\bseu raciocinio interno\b",
    ),
)
CROSS_CUSTOMER = Rule(
    "cross_customer_access",
    "block",
    (
        r"\b(cliente|customer|user|usuario|merchant|lojista|conta|account)[ _-]?\d{3,}\b.{0,60}\b(dados|data|vendas|sales|transa|saldo|balance|terminal|repasse|settlement|conta|case|chamado)",
        r"\b(dados|data|vendas|sales|transa|saldo|repasse|settlement|chamado|cases?)\b.{0,60}\b(de outro|of another|other|outros?|outras?|de terceiros|another)\b.{0,15}\b(cliente|customer|user|merchant|lojista|estabelecimento|conta)",
        r"\b(show|mostre|me (de|da|passe)|list|liste|consulte?)\b.{0,40}\b(cliente|customer)\s*[a-z]*\d{2,}\b",
        r"\b(como|as|as if i were|fingindo ser|se passando por)\b.{0,20}\b(cliente|customer|user)[ _-]?\d{2,}",
        r"\buser_?id\s*[:=]\s*\w+",
    ),
)
DATA_DUMP = Rule(
    "bulk_data_request",
    "block",
    (
        r"\b(dump|export|exporte|list|liste|mostre|show|todos|all|every)\b.{0,30}\b(all|todos|todas|every|entire|inteir[oa]s?)\b.{0,30}\b(customer|cliente|record|registro|user|usuario|transa|card|cart)",
        r"\b(select|drop|delete|update|insert)\b.{1,40}\b(from|table|into|where)\b",
        r"\bunion select\b|;\s*--",
    ),
)
CREDENTIALS = Rule(
    "credential_harvest",
    "block",
    (
        r"\b(api[ _-]?key|secret|token|password|senha)\b.{0,30}\b(of the system|do sistema|da api|do servidor|server|internal|interno|minimax|tavily|admin)",
        r"\b(env|environment|\.env|variaveis de ambiente)\b.{0,20}\b(var|show|print|mostre|conteudo)",
    ),
)
HIGH_RISK = Rule(
    "fraud_or_security_report",
    "info",
    (
        r"\b(fraude|fraud|golpe|scam|clonad|clone[ds]?|roubad|stolen|furtad|invadid|hacked|hackead|phishing|nao reconhe[cç]o|unrecognized|did not make|nao fiz (essa|esta) (compra|venda|transa))",
    ),
)
OFF_TOPIC_HARMFUL = Rule(
    "harmful_request",
    "block",
    (
        r"\b(como|how to)\b.{0,30}\b(lavar dinheiro|launder money|fraudar|clonar cart|clone (a )?card|skimm|carding|burlar (o )?antifraude|bypass (the )?antifraud)",
    ),
)

RULES: tuple[Rule, ...] = (
    INJECTION,
    PROMPT_EXTRACTION,
    CROSS_CUSTOMER,
    DATA_DUMP,
    CREDENTIALS,
    OFF_TOPIC_HARMFUL,
    HIGH_RISK,
)


@dataclass
class InputScreen:
    flags: list[dict[str, str]] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(f["severity"] == "block" for f in self.flags)

    @property
    def block_rule(self) -> str | None:
        return next((f["rule"] for f in self.flags if f["severity"] == "block"), None)

    def has(self, rule: str) -> bool:
        return any(f["rule"] == rule for f in self.flags)


def screen_input(
    message: str, recent_customer_turns: list[str] | None = None, own_customer_id: str | None = None
) -> InputScreen:
    result = InputScreen()
    texts = views(message)
    own = canonical(own_customer_id or "")
    for rule in RULES:
        for view in texts:
            if rule.matches(view):
                if rule is CROSS_CUSTOMER and own and _only_mentions_self(view, own):
                    continue
                result.flags.append(
                    {"layer": "input", "rule": rule.name, "severity": rule.severity, "detail": "current_message"}
                )
                break
    # Multi-turn injection: instructions split across turns ("ignore all" + "previous instructions").
    if recent_customer_turns and not result.blocked:
        joined = " ".join(canonical(t) for t in [*recent_customer_turns[-2:], message])
        for rule in (INJECTION, PROMPT_EXTRACTION):
            if rule.matches(joined):
                result.flags.append(
                    {
                        "layer": "input",
                        "rule": f"multi_turn_{rule.name}",
                        "severity": "block",
                        "detail": "split_across_turns",
                    }
                )
                break
    if contains_sensitive(message):
        result.flags.append(
            {"layer": "input", "rule": "sensitive_data_shared", "severity": "warn", "detail": "redacted_before_storage"}
        )
    for f in result.flags:
        metrics.GUARDRAIL_TRIGGERS.labels(layer="input", rule=f["rule"]).inc()
    return result


def _only_mentions_self(view: str, own: str) -> bool:
    ids = set(re.findall(r"\b[a-z]*[ _-]?\d{3,}\b", view))
    ids = {i.replace(" ", "").replace("_", "").replace("-", "") for i in ids}
    return bool(ids) and ids <= {own.replace("_", "").replace("-", "")}


REFUSALS = {
    "prompt_injection": {
        "pt": "Não posso seguir instruções que tentam alterar minhas regras de funcionamento. Posso ajudar com dúvidas sobre produtos Getnet ou sobre a sua conta.",
        "en": "I can't follow instructions that try to change how I operate. I'm happy to help with Getnet products or your account.",
    },
    "system_prompt_extraction": {
        "pt": "Não posso compartilhar minhas instruções internas. Posso ajudar com dúvidas sobre produtos Getnet ou sobre a sua conta.",
        "en": "I can't share my internal instructions. I'm happy to help with Getnet products or your account.",
    },
    "cross_customer_access": {
        "pt": "Por segurança, só posso acessar os dados da conta autenticada nesta conversa.",
        "en": "For security reasons I can only access data for the account authenticated in this conversation.",
    },
    "bulk_data_request": {
        "pt": "Não posso exportar ou listar dados em massa. Posso consultar informações específicas da sua própria conta.",
        "en": "I can't export or list data in bulk. I can look up specific information about your own account.",
    },
    "credential_harvest": {
        "pt": "Não posso compartilhar credenciais, chaves ou configurações internas.",
        "en": "I can't share credentials, keys or internal configuration.",
    },
    "harmful_request": {
        "pt": "Não posso ajudar com esse pedido. Se você suspeita de fraude, posso abrir um atendimento de segurança.",
        "en": "I can't help with that request. If you suspect fraud, I can open a security support case.",
    },
}


def refusal(rule: str, language: str) -> str:
    base = rule.removeprefix("multi_turn_")
    lang = "pt" if language == "pt" else "en"
    return REFUSALS.get(base, REFUSALS["prompt_injection"])[lang]
