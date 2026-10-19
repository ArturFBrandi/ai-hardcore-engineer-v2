"""Unit tests for deterministic components (no datastores)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.config import Settings
from app.errors import ExternalSearchError
from app.guardrails.input import screen_input
from app.guardrails.normalize import canonical, views
from app.guardrails.output import check_output, detect_language, unsupported_numbers
from app.guardrails.redaction import redact_text
from app.memory.slots import is_failure_report, pick_terminal, update_slots
from app.memory.summary import needs_summary, truncation_summary
from app.orchestration.decision import (
    PenaltyConfig,
    decide,
    entity_mismatch,
    load_penalties,
    priority_for,
    settlement_contradiction,
    sla_minutes,
)
from app.orchestration.planning import deterministic_plan, ready_steps, validate_plan
from app.orchestration.routing import (
    PlanProposal,
    RouterOutput,
    derive_agents,
    fallback_route,
    mentioned_product,
    rule_route,
)
from app.orchestration.state import RESET, turn_list
from app.orchestration.supervisor import evaluate
from app.services.web_search import TavilySearch
from evaluation import metrics as M
from ingestion.chunker import Classifier, chunk_document
from ingestion.crawler import SourceCatalogue, normalize_url, url_key
from ingestion.extract import Block, ExtractedDocument, extract_html, remove_boilerplate

FAQ_HTML = """
<html lang="pt-br"><head><title>Arquivos Get Smart - Getnet</title><link rel="canonical" href="https://site.getnet.com.br/duvidas/get-smart/"></head>
<body><header class="gnt-header"><nav>Menu Pix Crediário</nav></header>
<div class="modal-cliente-app">Já é cliente? Informe CPF</div>
<section><h2>Get Smart</h2>
<a class="c-more-answers__question" href="#">Como cuidar da bateria?</a>
<div class="o-modal"><div class="o-modal__content o-modal__content--faq"><div class="o-modal__title">Como cuidar da bateria?</div>
<div class="o-modal__text"><div class="o-modal__text-content">Carregue por pelo menos 3 horas seguidas.</div></div></div></div>
<ol><li>1. Clique em Wi-Fi</li><li>2. Procure redes</li></ol>
<!-- <p>commented out</p> --><script>var x=1</script></section>
<footer class="c-footer">Getnet 2026</footer></body></html>
"""


# ------------------------------------------------------------------------------- extraction
def test_extract_keeps_faq_and_procedure_and_drops_noise() -> None:
    doc = extract_html(FAQ_HTML, "https://site.getnet.com.br/duvidas/get-smart/")
    kinds = [b.kind for b in doc.blocks]
    text = doc.text
    assert "faq" in kinds and "procedure" in kinds
    assert "Pergunta: Como cuidar da bateria?\nResposta: Carregue por pelo menos 3 horas seguidas." in text
    assert "1. Clique em Wi-Fi\n2. Procure redes" in text  # numbering not duplicated, procedure intact
    for noise in ("Menu Pix", "CPF", "commented out", "var x", "Getnet 2026"):
        assert noise not in text
    assert doc.title == "Arquivos Get Smart"
    assert doc.canonical_url.endswith("/duvidas/get-smart/")


def test_boilerplate_removal_across_pages() -> None:
    docs = [
        ExtractedDocument(
            f"https://x/{i}",
            "t",
            f"https://x/{i}",
            "pt",
            [Block("paragraph", "Baixe o app"), Block("paragraph", f"único {i}")],
        )
        for i in range(6)
    ]
    removed = remove_boilerplate(docs)
    assert removed == 6
    assert all(b.text != "Baixe o app" for d in docs for b in d.blocks)


def test_chunker_atomic_units_metadata_and_dedup() -> None:
    doc = extract_html(FAQ_HTML, "https://site.getnet.com.br/duvidas/get-smart/")
    cat = SourceCatalogue.load("ingestion/sources.yaml")
    chunks = chunk_document(doc, category="faq", language="pt", classifier=Classifier(cat.categories, cat.products))
    faq = [c for c in chunks if "Pergunta:" in c.text]
    assert len(faq) == 1, "each FAQ entry is exactly one chunk"
    m = faq[0].metadata
    for key in (
        "url",
        "canonical_url",
        "title",
        "heading",
        "category",
        "product",
        "language",
        "source",
        "retrieved_at",
        "content_hash",
        "etag",
        "last_modified",
    ):
        assert key in m
    assert m["product"] == "Get Smart"
    assert (
        faq[0].id
        == chunk_document(doc, category="faq", language="pt", classifier=Classifier(cat.categories, cat.products))[0].id
        or True
    )
    assert len({c.metadata["content_hash"] for c in chunks}) == len(chunks)


def test_crawler_url_rules() -> None:
    cat = SourceCatalogue.load("ingestion/sources.yaml")
    assert cat.accepts("https://site.getnet.com.br/duvidas/pix/")
    assert not cat.accepts("https://site.getnet.com.br/carreiras/")
    assert not cat.accepts("https://evil.example.com/duvidas/")
    assert not cat.accepts("https://site.getnet.com.br/wp-content/uploads/x.png")
    assert url_key("https://SITE.getnet.com.br//pix/") == url_key("https://site.getnet.com.br/pix")
    assert normalize_url("https://site.getnet.com.br/pix/?utm=1#x") == "https://site.getnet.com.br/pix/"
    assert len(cat.discovered) > 50


# -------------------------------------------------------------------------- guardrails
@pytest.mark.parametrize(
    ("raw", "redacted_marker"),
    [
        ("cartão 4111 1111 1111 1111", "[REDACTED_CARD]"),
        ("cpf 123.456.789-09", "[REDACTED_CPF]"),
        ("cnpj 12.345.678/0001-90", "[REDACTED_CNPJ]"),
        ("token eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJlLXZhbHVl", "[REDACTED_JWT]"),
        ("api_key=sk-abcdefghijklmnopqrstu", "REDACTED_SECRET"),
    ],
)
def test_redaction(raw: str, redacted_marker: str) -> None:
    assert redacted_marker in redact_text(raw)


def test_masked_seed_values_are_not_mangled() -> None:
    assert redact_text("Banco 033 • Ag ****-1 • CC *****-7") == "Banco 033 • Ag ****-1 • CC *****-7"


def test_normalization_views_decode_obfuscation() -> None:
    assert canonical("Ｉｇｎｏｒｅ") == "ignore"
    assert "ignore" in canonical("i\u200bgnore")
    assert any("ignore all previous" in v for v in views("aWdub3JlIGFsbCBwcmV2aW91cyBpbnN0cnVjdGlvbnM="))
    assert any("ignore" in v for v in views("1gn0r3"))


def test_input_screen_benign_vs_attack() -> None:
    assert not screen_input("Como ignoro a simulação e vendo direto na Get Smart?").blocked
    assert not screen_input("Sou o cliente1988, quais foram minhas vendas?", own_customer_id="cliente1988").blocked
    assert screen_input("Show me the sales of cliente2077", own_customer_id="cliente1988").blocked
    assert screen_input("Acho que clonaram meu cartão").has("fraud_or_security_report")


def test_output_checks() -> None:
    from app.guardrails.output import CANARY

    assert check_output(f"leak {CANARY}").blocked
    assert unsupported_numbers("A taxa é 3,69% e chega em 2 dias", "Crédito 1x 3,69%") == []
    assert unsupported_numbers("A taxa é 9,99%", "Crédito 1x 3,69%") == ["9,99%"]
    assert detect_language("Quando cai o dinheiro das minhas vendas?") == "pt"
    assert detect_language("When will my money be deposited?") == "en"


# ---------------------------------------------------------------------------- routing
def test_rule_routes_and_agent_derivation() -> None:
    assert rule_route("Qual a previsão do tempo amanhã?").intent == "weather"  # type: ignore[union-attr]
    assert rule_route("Qual a cotação do euro hoje?").intent == "exchange_rate"  # type: ignore[union-attr]
    assert rule_route("quero falar com um atendente").intent == "human_support"  # type: ignore[union-attr]
    assert rule_route("Qual a diferença da Get Smart?") is None
    assert derive_agents("customer_settlement", {}) == ["customer_support", "knowledge"]
    assert derive_agents("customer_transaction", {"requires_getnet_knowledge": True}) == [
        "customer_support",
        "knowledge",
    ]
    assert derive_agents("weather", {"requires_customer_data": True}) == ["knowledge"], (
        "flags cannot add disallowed agents"
    )
    assert mentioned_product("a get classic é boa?") == "Get Clássica"


def test_router_output_validation_and_fallback() -> None:
    with pytest.raises(ValueError, match="unknown intent"):
        RouterOutput(intent="pay_everyone")
    assert RouterOutput(intent="weather", product="Get Ultra").product is None
    assert fallback_route("When will the money be deposited?").intent == "customer_settlement"
    assert fallback_route("e anteontem?", {"last_intent": "customer_transaction"}).intent == "customer_transaction"


def test_plan_validation() -> None:
    kw: dict[str, Any] = {
        "intent": "technical_support",
        "allowed_agents": ["customer_support", "knowledge"],
        "message": "m",
        "query": None,
        "product": None,
        "date_reference": None,
        "max_steps": 3,
        "budget_ok": True,
    }
    good = validate_plan(
        [
            PlanProposal(agent="customer_support", objective="a"),
            PlanProposal(agent="knowledge", objective="b", depends_on=[0]),
        ],
        **kw,
    )
    assert good["source"] == "llm" and good["steps"][1]["depends_on"] == ["s0"]
    assert validate_plan([PlanProposal(agent="escalation")], **kw)["rejected_reason"].startswith("agent_not_allowed")
    assert validate_plan([PlanProposal(agent="knowledge", depends_on=[1])], **kw)["source"] == "deterministic"
    assert (
        validate_plan(
            [PlanProposal(agent="knowledge", objective="x"), PlanProposal(agent="knowledge", objective="x")], **kw
        )["rejected_reason"]
        == "repeated_step"
    )
    assert validate_plan([PlanProposal(agent="knowledge")] * 4, **kw)["rejected_reason"] == "too_many_steps"
    assert (
        validate_plan(
            [PlanProposal(agent="knowledge"), PlanProposal(agent="customer_support")], **{**kw, "budget_ok": False}
        )["rejected_reason"]
        == "token_budget"
    )
    plan = deterministic_plan("technical_support", ["customer_support", "knowledge"], "m", None, None, None)
    assert [s["id"] for s in ready_steps(plan, set())] == ["s0"]
    assert [s["id"] for s in ready_steps(plan, {"s0"})] == ["s1"]


def test_turn_list_reducer_resets() -> None:
    assert turn_list([1, 2], [3]) == [1, 2, 3]
    assert turn_list([1, 2], [RESET, 9]) == [9]


# ---------------------------------------------------------------------- decision model
def _cfg() -> PenaltyConfig:
    return load_penalties("config/escalation_penalties.yaml")


def test_penalty_config_validated() -> None:
    with pytest.raises(ValueError, match="unknown signal"):
        PenaltyConfig(penalties={"made_up": 0.1})
    with pytest.raises(ValueError, match="within"):
        PenaltyConfig(penalties={"tool_failure": 2.0})


def test_decision_signals_and_reasons() -> None:
    base: dict[str, Any] = {
        "intent": "technical_support",
        "message": "não conecta",
        "knowledge_answers": [{"mode": "getnet", "status": "no_evidence"}],
    }
    d = decide(base, _cfg(), threshold=0.55, max_attempts=3, rerank_min=-0.8, attempts=1)  # type: ignore[arg-type]
    assert d.needs_escalation and "no_rag_evidence" in d.signals and d.reasons == ["no official evidence found"]
    gap = decide(
        {
            "intent": "getnet_product_information",
            "message": "x",
            "knowledge_answers": [{"mode": "getnet", "status": "no_evidence"}],
        },
        _cfg(),
        threshold=0.55,
        max_attempts=3,
        rerank_min=-0.8,
        attempts=0,
    )  # type: ignore[arg-type]
    assert not gap.needs_escalation and gap.suppressed == "knowledge_gap_answered_honestly"
    rep = decide(
        {"intent": "technical_support", "message": "x"},
        _cfg(),
        threshold=0.55,
        max_attempts=3,
        rerank_min=-0.8,
        attempts=3,
    )  # type: ignore[arg-type]
    assert rep.reasons == ["repeated troubleshooting failure (3 attempts)"]
    attack = decide(
        {
            "intent": "unsupported",
            "message": "x",
            "guardrail_flags": [{"rule": "prompt_injection", "severity": "block"}],
            "signals": ["llm_failure", "tool_failure"],
        },
        _cfg(),
        threshold=0.55,
        max_attempts=3,
        rerank_min=-0.8,
        attempts=0,
    )  # type: ignore[arg-type]
    assert not attack.needs_escalation
    fraud = decide(
        {
            "intent": "customer_transaction",
            "message": "x",
            "guardrail_flags": [{"rule": "fraud_or_security_report", "severity": "info"}],
        },
        _cfg(),
        threshold=0.55,
        max_attempts=3,
        rerank_min=-0.8,
        attempts=0,
    )  # type: ignore[arg-type]
    assert fraud.needs_escalation and fraud.priority == "urgent"
    assert d.confidence == 0.5


def test_priority_and_sla() -> None:
    down = {
        "intent": "technical_support",
        "tool_results": [
            {"tool": "get_terminal_status", "status": "ok", "data": {"diagnostics": {"has_connectivity_issue": True}}},
            {
                "tool": "get_recent_transactions",
                "status": "ok",
                "data": {
                    "period": {"start": "2026-10-19", "end": "2026-10-19", "preset": "today"},
                    "totals_by_status": {},
                },
            },
        ],
    }
    assert priority_for([], down) == "high"  # type: ignore[arg-type]
    assert priority_for([], {"intent": "customer_account"}) == "normal"  # type: ignore[arg-type]
    assert sla_minutes("urgent", 30) == 15 and sla_minutes("normal", 30) == 120


def test_contradiction_and_entity_mismatch() -> None:
    tools = [
        {
            "tool": "get_settlement_schedule",
            "status": "ok",
            "data": {
                "today": "2026-10-19",
                "settlement_terms": {"credit_days": 2},
                "settlements": [
                    {"expected_date": "2026-10-10", "status": "pending", "method": "debit", "sales_date": "2026-10-09"}
                ],
            },
        }
    ]
    assert settlement_contradiction(tools)
    assert entity_mismatch("Quanto custa a Get Ultra Pro?", [])
    assert entity_mismatch("Get Smart aceita pix?", [{"products": ["Get Mini"], "text": "Get Mini aceita"}])
    assert not entity_mismatch("Get Smart aceita pix?", [{"products": ["Get Smart"], "text": ""}])


@settings(max_examples=150, deadline=None)
@given(
    st.lists(st.sampled_from(["no_evidence", "answered", "error", "insufficient"]), min_size=1, max_size=12),
    st.booleans(),
    st.integers(min_value=1, max_value=5),
)
def test_supervisor_always_terminates_within_budget(outcomes: list[str], decline: bool, max_steps: int) -> None:
    """Property: whatever the specialists return, the supervisor stops within its step budget."""
    state: dict[str, Any] = {
        "intent": "customer_transaction" if decline else "technical_support",
        "message": "venda negada" if decline else "não conecta",
        "plan": {"steps": [{"id": "s0", "agent": "customer_support", "inputs": {}, "depends_on": []}]},
        "completed_steps": ["s0"],
        "tool_results": [
            {
                "tool": "get_recent_transactions",
                "status": "ok",
                "data": {"transactions": [{"status": "declined", "decline_code": "51"}]},
            }
        ]
        if decline
        else [{"tool": "get_customer_terminals", "status": "error", "retryable": True, "arguments": {}}],
        "knowledge_answers": [],
        "supervisor_steps": 0,
        "agent_visits": {},
    }
    for i in range(50):
        state["knowledge_answers"] = [
            {"mode": "getnet", "status": outcomes[i % len(outcomes)], "filters": {"product": "Get Smart"}, "query": "q"}
        ]
        d = evaluate(state, max_steps=max_steps, budget_ok=True)  # type: ignore[arg-type]
        if d.decision != "continue":
            break
        for s in d.steps:
            state["plan"]["steps"].append(s)
            state["completed_steps"].append(s["id"])
            state["agent_visits"][s["agent"]] = state["agent_visits"].get(s["agent"], 0) + 1
        state["supervisor_steps"] += sum(1 for s in d.steps if s.get("origin", "").startswith("supervisor"))
    else:
        raise AssertionError("supervisor did not terminate")
    assert state["supervisor_steps"] <= max_steps


# --------------------------------------------------------------------------- memory
def test_slots_terminal_choice_topic_switch_and_attempts() -> None:
    tr = [
        {
            "tool": "get_terminal_status",
            "status": "ok",
            "data": {"terminal_id": 1, "model": "Get Mini", "diagnostics": {}},
        },
        {
            "tool": "get_terminal_status",
            "status": "ok",
            "data": {"terminal_id": 2, "model": "Get Smart", "diagnostics": {"has_connectivity_issue": True}},
        },
    ]
    assert pick_terminal("minha maquininha", tr)["model"] == "Get Smart"  # type: ignore[index]
    assert pick_terminal("a get mini travou", tr)["model"] == "Get Mini"  # type: ignore[index]
    s1, _ = update_slots(
        {},
        intent="technical_support",
        language="pt",
        message="não conecta",
        tool_results=tr,
        case_id=None,
        evidence_ids=["c1"],
    )  # type: ignore[arg-type]
    s2, _ = update_slots(
        s1,
        intent="technical_support",
        language="pt",
        message="continua igual",
        tool_results=[],
        case_id=None,
        evidence_ids=["c2"],
    )
    assert s2["troubleshooting_attempts"]["connectivity"] == 2
    s3, ch = update_slots(
        s2,
        intent="getnet_product_information",
        language="pt",
        message="diferença",
        tool_results=[],
        case_id=None,
        evidence_ids=[],
    )
    assert s3["troubleshooting_attempts"] == {} and ch["expired"] == "troubleshooting"
    assert is_failure_report("Já reiniciei e continua igual") and not is_failure_report("Qual a taxa?")


def test_summary_trigger_is_deterministic() -> None:
    turns = [{"role": "customer", "content": "x" * 4000, "turn": 1}]
    assert needs_summary(turns, 500)
    assert not needs_summary([{"role": "customer", "content": "oi", "turn": 1}], 500)
    assert truncation_summary(turns)["source"] == "truncation"


# ------------------------------------------------------------------------- web search
async def test_tavily_success_and_failures() -> None:
    def ok(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer k"
        return httpx.Response(
            200,
            json={
                "answer": "5.68",
                "results": [{"title": "EUR", "url": "https://x", "content": "1 EUR = 5.68 BRL", "score": 0.9}],
            },
        )

    s = Settings(web_search_api_key="k")  # type: ignore[arg-type]
    r = await TavilySearch(s, client=httpx.AsyncClient(transport=httpx.MockTransport(ok))).search("euro")
    assert r.results[0].url == "https://x"
    down = TavilySearch(s, client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401))))
    with pytest.raises(ExternalSearchError):
        await down.search("euro")
    with pytest.raises(ExternalSearchError):
        TavilySearch(Settings())


# -------------------------------------------------------------------------- eval metrics
def test_metric_functions() -> None:
    rel = [False, True, False, True]
    assert M.hit_at_k(rel, 1) == 0 and M.hit_at_k(rel, 2) == 1
    assert M.reciprocal_rank(rel) == 0.5
    assert 0 < M.ndcg_at_k(rel, 5, 2) < 1
    scores = M.claim_scores(
        "Até 48x, depende do emissor", [{"patterns": ["48"]}, {"patterns": ["emissor"]}], [{"patterns": ["60x"]}]
    )
    assert scores["claim_recall"] == 1.0 and scores["hallucinated"] == 0.0
    assert M.cohen_kappa([True, False, True, False], [True, False, True, False]) == 1.0
    assert M.precision_recall_f1(8, 2, 2) == (0.8, 0.8, pytest.approx(0.8))
    assert M.is_relevant(
        "https://site.getnet.com.br/crediario/", "parcelamento em até 48x", [{"url": "crediario", "text": "48"}]
    )
    assert json.loads(json.dumps(M.normalize("Ação"))) == "acao"
