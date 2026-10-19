"""Real LangGraph graph with a controlled (fake) LLM. Assertions on structure, never on prose."""

from __future__ import annotations

from app.errors import ProviderError
from app.rag.schemas import RetrievalResult
from tests.orchestration.conftest import events, interval


async def test_workflow1_settlement_specialists_run_in_parallel(turn) -> None:  # type: ignore[no-untyped-def]
    r = await turn("When will the money from yesterday's sales be deposited?")
    s = r.state
    assert s["intent"] == "customer_settlement"
    assert s["route"] == ["customer_support", "knowledge"]
    plan_ev = events(s, "router", "plan")[0]["detail"]
    assert set(plan_ev["first_superstep"]) == {"s0", "s1"}
    cs = interval(events(s, "customer_support", "exited")[0])
    kn = interval(events(s, "knowledge", "exited")[0])
    assert cs[0] < kn[1] and kn[0] < cs[1], "specialists must overlap in time (same superstep)"
    tools = {t["tool"] for t in s["tool_results"]}
    assert {"get_settlement_schedule", "get_recent_transactions"} <= tools
    assert s["status"] == "answered"
    assert "R$" in s["final_answer"] or "BRL" in s["final_answer"]


async def test_workflow2_supervisor_dependency_with_product_filter(turn) -> None:  # type: ignore[no-untyped-def]
    r = await turn("My card machine won't connect to the internet, what should I do?")
    s = r.state
    assert s["intent"] == "technical_support"
    decisions = [e["detail"] for e in events(s, "supervisor", "decision")]
    first = decisions[0]
    assert first["decision"] == "continue"
    assert first["reason"] == "terminal_model_known"
    assert first["product_filter"] == "Get Smart"
    retrieved = events(s, "knowledge", "retrieved")[0]["detail"]
    assert retrieved["filters"] == {"product": "Get Smart", "category": "terminal_troubleshooting"}
    cs_end = interval(events(s, "customer_support", "exited")[0])[1]
    kn_start = interval(events(s, "knowledge", "exited")[0])[0]
    assert cs_end <= kn_start, "knowledge must run after customer support (data dependency)"
    # evidence is Get Smart-specific or model-agnostic (generic troubleshooting) — never another model's
    top = s["retrieved_documents"][0]
    assert not top.get("products") or "Get Smart" in top["products"]
    assert s["citations"], "the answer cites the model-specific source"


async def test_web_path_and_honest_outage(turn, fake_web) -> None:  # type: ignore[no-untyped-def]
    r = await turn("What's the euro exchange rate today?")
    s = r.state
    assert s["intent"] == "exchange_rate"
    assert s["route_source"] == "rule"
    assert s["web_results"]
    assert s["status"] == "answered"
    fake_web.down = True
    r2 = await turn("What's the weather forecast in Porto Alegre tomorrow?")
    s2 = r2.state
    assert s2["intent"] == "weather"
    assert s2["status"] == "answered", "web outage degrades to 'unavailable', never a support case"
    assert s2["case_id"] is None
    assert "external information" in s2["final_answer"].lower() or "informações externas" in s2["final_answer"].lower()


async def test_insufficient_evidence_escalates_customer_problem(turn, container, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def empty(query, filters=None, **kw):  # type: ignore[no-untyped-def]
        return RetrievalResult(query=query, documents=[], filters=filters or {}, diagnostics={"selected": 0})

    monkeypatch.setattr(container.retriever, "retrieve", empty)
    r = await turn("Minha maquininha não conecta na internet")
    s = r.state
    assert "no_rag_evidence" in s["signals"]
    reasons = [e["detail"]["reason"] for e in events(s, "supervisor", "decision")]
    assert "rag_empty_under_filter" in reasons, "one broadened retrieval before declaring no evidence"
    assert s["needs_escalation"] is True
    assert s["status"] == "awaiting_human"
    assert s["case_id"]


async def test_knowledge_gap_is_answered_honestly_without_case(turn, container, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def empty(query, filters=None, **kw):  # type: ignore[no-untyped-def]
        return RetrievalResult(query=query, documents=[], filters=filters or {}, diagnostics={"selected": 0})

    monkeypatch.setattr(container.retriever, "retrieve", empty)
    r = await turn("What's the monthly price of the Get Ultra Pro card machine?")
    s = r.state
    assert s["case_id"] is None
    assert "no_rag_evidence" in s["signals"]
    assert "verified" in s["final_answer"].lower() or "verificadas" in s["final_answer"].lower()


async def test_invalid_router_output_uses_deterministic_fallback(turn, fake_llm) -> None:  # type: ignore[no-untyped-def]
    fake_llm.script("router", "not json", '{"intent": "made_up_intent"}')
    r = await turn("Quais foram minhas vendas de ontem?")
    s = r.state
    assert s["route_source"] == "fallback"
    assert s["intent"] == "customer_transaction"
    assert "customer_support" in s["route"]


async def test_llm_outage_degrades_to_deterministic_paths(turn, fake_llm) -> None:  # type: ignore[no-untyped-def]
    fake_llm.fail_all = ProviderError("down", retryable=True)
    r = await turn("When will the money from yesterday's sales be deposited?")
    s = r.state
    assert s["route_source"] == "fallback"
    assert any(t["tool"] == "get_settlement_schedule" and t["status"] == "ok" for t in s["tool_results"]), (
        "coverage guard"
    )
    assert s["final_answer"]
    assert "llm_failure" in s["signals"]


async def test_router_never_answers_and_rule_routes_explicit_human(turn) -> None:  # type: ignore[no-untyped-def]
    r = await turn("Quero falar com um atendente humano")
    s = r.state
    assert s["intent"] == "human_support"
    assert s["route_source"] == "rule"
    assert s["status"] == "awaiting_human"
    assert "customer asked for a human agent" in s["escalation_reasons"]


async def test_supervisor_step_budget_enforced(turn, container, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(container.settings, "supervisor_max_steps", 1)

    async def empty(query, filters=None, **kw):  # type: ignore[no-untyped-def]
        return RetrievalResult(query=query, documents=[], filters=filters or {}, diagnostics={"selected": 0})

    monkeypatch.setattr(container.retriever, "retrieve", empty)
    r = await turn("Minha maquininha está com erro de transação negada, o que faço?")
    s = r.state
    assert s["supervisor_steps"] <= 1


async def test_failed_specialist_node_cannot_loop_the_supervisor(turn, container, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Regression: a crashing node used to leave its step pending → rescheduled until the recursion limit."""

    async def boom(query, filters=None, **kw):  # type: ignore[no-untyped-def]
        raise ValueError("reranker model missing")

    monkeypatch.setattr(container.retriever, "retrieve", boom)
    r = await turn("Minha maquininha não conecta na internet")
    s = r.state
    assert s["final_answer"]
    assert any(e.startswith("knowledge:") for e in s["errors"])
    assert s["supervisor_steps"] <= container.settings.supervisor_max_steps
