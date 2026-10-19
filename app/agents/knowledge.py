"""Knowledge Agent.

* **Getnet mode** — hybrid RAG is authoritative. No evidence → honest "not verified" answer (no LLM call,
  no pretrained facts). Generation cites evidence ids; code keeps only citations to evidence actually
  given *and* referenced; numbers must appear in the evidence (one stricter regeneration, then an
  extractive fallback).
* **External mode** — Tavily web search; search down → honest "unavailable" (no escalation).
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from app.agents.formatting import message
from app.agents.prompts import evidence_block, render_messages, web_block
from app.errors import ExternalSearchError, ProviderError, RetrievalError
from app.guardrails.output import unsupported_numbers
from app.llm.base import structured
from app.memory.slots import family
from app.orchestration.runtime import GraphContext, event, node
from app.orchestration.state import AgentState, EvidenceEntry, KnowledgeAnswer, PlanStep, WebResultEntry
from app.rag.schemas import RetrievalFilters


class RagAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=3000)
    used_evidence: list[str] = Field(default_factory=list)
    insufficient: bool = False


class WebAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)
    used_results: list[str] = Field(default_factory=list)
    insufficient: bool = False


_CITE = re.compile(r"\[(E\d+)\]")


def _evidence_ids(step_id: str, n: int) -> list[str]:
    base = int(step_id.lstrip("s") or 0) * 10 + 1
    return [f"E{base + k}" for k in range(n)]


def extractive_answer(evidence: list[EvidenceEntry], lang: str) -> str:
    top = evidence[0]
    body = top["text"].split("\n", 1)[-1].strip()
    snippet = body[:450].rsplit(" ", 1)[0] + ("…" if len(body) > 450 else "")
    lead = "Segundo a fonte oficial da Getnet" if lang == "pt" else "According to Getnet's official source"
    return f"{lead} ({top['title']}): {snippet} [{top['evidence_id']}]"


async def getnet_mode(state: AgentState, ctx: GraphContext, step: PlanStep, lang: str) -> dict[str, Any]:
    inputs = step.get("inputs") or {}
    query = inputs.get("query") or state["message"]
    broaden = bool(inputs.get("broaden"))
    filters = RetrievalFilters(
        product=None if broaden else inputs.get("product_filter"),
        category=None if broaden else inputs.get("category"),
    )
    if ctx.retriever is None:
        return {
            "answer": KnowledgeAnswer(
                step_id=step["id"],
                mode="getnet",
                status="error",
                answer=message("kb_unavailable", lang),
                used_evidence=[],
                query=query,
                filters={},
            ),
            "evidence": [],
            "events": [],
        }
    t0 = time.perf_counter()
    try:
        result = await ctx.retriever.retrieve(query, filters)
    except RetrievalError:
        return {
            "answer": KnowledgeAnswer(
                step_id=step["id"],
                mode="getnet",
                status="error",
                answer=message("kb_unavailable", lang),
                used_evidence=[],
                query=query,
                filters=filters.model_dump(exclude_none=True),
            ),
            "evidence": [],
            "events": [event("knowledge", "retrieval_failed", {"query": query[:120]}, t0)],
        }
    docs = result.documents
    # Troubleshooting follow-ups: don't serve the same steps again (slot from previous turns).
    slots = state.get("slots") or {}
    used_before = (
        set(slots.get("troubleshooting_evidence_used") or [])
        if family(state.get("intent")) == "troubleshooting"
        else set()
    )
    if used_before:
        fresh = [d for d in docs if d.chunk_id not in used_before]
        if len(fresh) >= 1:
            docs = fresh
    ids = _evidence_ids(step["id"], len(docs))
    evidence = [
        EvidenceEntry(
            evidence_id=eid,
            chunk_id=d.chunk_id,
            title=d.title,
            url=d.url,
            heading=d.heading,
            category=d.category,
            product=d.product,
            products=d.products,
            retrieved_at=d.retrieved_at,
            rerank_score=d.rerank_score,
            fusion_score=d.fusion_score,
            text=d.text,
            step_id=step["id"],
        )
        for eid, d in zip(ids, docs, strict=True)
    ]
    events = [
        event(
            "knowledge",
            "retrieved",
            {**result.diagnostics, "query": query[:160], "excluded_previous": len(used_before)},
            t0,
        )
    ]
    base = KnowledgeAnswer(
        step_id=step["id"],
        mode="getnet",
        query=query,
        filters=filters.model_dump(exclude_none=True),
        top_rerank_score=result.top_rerank_score,
        broadened=broaden,
    )
    if not evidence:
        return {
            "answer": {**base, "status": "no_evidence", "answer": message("no_evidence", lang), "used_evidence": []},
            "evidence": [],
            "events": events,
        }

    task = {
        "question": state["message"],
        "objective": step.get("objective"),
        "language": lang,
        "terminal_model": inputs.get("product_filter"),
        "exclude_steps_already_given": bool(used_before),
    }
    grounding = "\n".join(e["text"] for e in evidence) + "\n" + state["message"]
    msgs = render_messages("knowledge_rag", task, blocks=[evidence_block([dict(e) for e in evidence])])
    status, answer, used = "answered", "", [e["evidence_id"] for e in evidence[:1]]
    t1 = time.perf_counter()
    try:
        out, _ = await structured(ctx.llm, task="knowledge_rag", messages=msgs, schema=RagAnswer, max_tokens=700)
        bad = unsupported_numbers(out.answer, grounding)
        if bad:
            events.append(event("knowledge", "numeric_claims_unsupported", {"numbers": bad[:5]}))
            retry_msgs = [
                *msgs,
                {
                    "role": "user",
                    "content": f"These numbers are not in the evidence: {bad[:5]}. Rewrite using only numbers that appear in the evidence.",
                },
            ]
            out, _ = await structured(
                ctx.llm, task="knowledge_rag", messages=retry_msgs, schema=RagAnswer, max_tokens=700
            )
            if unsupported_numbers(out.answer, grounding):
                raise ProviderError("unsupported numeric claims after regeneration")
        valid = {e["evidence_id"] for e in evidence}
        cited = [c for c in dict.fromkeys([*out.used_evidence, *_CITE.findall(out.answer)]) if c in valid]
        answer = _CITE.sub(lambda m: m.group(0) if m.group(1) in valid else "", out.answer).strip()
        used = cited or used
        status = "insufficient" if out.insufficient else "answered"
    except ProviderError as exc:
        answer = extractive_answer(evidence, lang)
        status = "answered"
        events.append(event("knowledge", "generation_fallback", {"reason": exc.detail[:80]}))
    events.append(event("knowledge", "generated", {"status": status, "used_evidence": used}, t1))
    return {
        "answer": {**base, "status": status, "answer": answer, "used_evidence": used},
        "evidence": evidence,
        "events": events,
    }


async def web_mode(state: AgentState, ctx: GraphContext, step: PlanStep, lang: str) -> dict[str, Any]:
    query = (step.get("inputs") or {}).get("query") or state["message"]
    t0 = time.perf_counter()
    base = KnowledgeAnswer(step_id=step["id"], mode="web", query=query, filters={}, used_evidence=[])
    try:
        resp = await ctx.web.search(query, max_results=ctx.settings.web_search_max_results)
    except ExternalSearchError as exc:
        return {
            "answer": {**base, "status": "unavailable", "answer": message("web_unavailable", lang)},
            "web": [],
            "events": [event("knowledge", "web_search_failed", {"error": exc.detail[:80]}, t0)],
        }
    results = [
        WebResultEntry(
            title=r.title, url=r.url, content=r.content, score=r.score, retrieved_at=r.retrieved_at, step_id=step["id"]
        )
        for r in resp.results
    ]
    events = [
        event(
            "knowledge",
            "web_search",
            {"results": len(results), "latency_ms": resp.latency_ms, "query": query[:120]},
            t0,
        )
    ]
    if not results:
        return {
            "answer": {**base, "status": "unavailable", "answer": message("web_unavailable", lang)},
            "web": [],
            "events": events,
        }
    task = {"question": state["message"], "language": lang, "search_summary": (resp.answer or "")[:600]}
    try:
        out, _ = await structured(
            ctx.llm,
            task="knowledge_web",
            messages=render_messages("knowledge_web", task, blocks=[web_block([dict(r) for r in results])]),
            schema=WebAnswer,
            max_tokens=500,
        )
        answer, status = out.answer, ("insufficient" if out.insufficient else "answered")
    except ProviderError:
        top = results[0]
        lead = "Fonte" if lang == "pt" else "Source"
        answer, status = (
            f"{(resp.answer or top['content'][:300]).strip()} ({lead}: {top['title']} — {top['url']}, {top['retrieved_at'][:16]} UTC)",
            "answered",
        )
    return {"answer": {**base, "status": status, "answer": answer}, "web": results, "events": events}


@node("knowledge")
async def knowledge_node(state: AgentState, *, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    ctx = runtime.context
    lang = state.get("language", "pt")
    steps = [s for s in state.get("active_steps") or [] if s["agent"] == "knowledge"]

    async def one(step: PlanStep) -> dict[str, Any]:
        mode = (step.get("inputs") or {}).get("mode", "getnet")
        return await (web_mode(state, ctx, step, lang) if mode == "web" else getnet_mode(state, ctx, step, lang))

    outs = await asyncio.gather(*(one(s) for s in steps))
    answers = [o["answer"] for o in outs]
    evidence = [e for o in outs for e in o.get("evidence", [])]
    web = [w for o in outs for w in o.get("web", [])]
    return {
        "knowledge_answers": answers,
        "retrieved_documents": evidence,
        "web_results": web,
        "completed_steps": [s["id"] for s in steps],
        "events": [e for o in outs for e in o["events"]],
    }
