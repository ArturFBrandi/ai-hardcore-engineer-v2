"""Response Synthesizer: one coherent, grounded answer in the merchant's language.

* Single grounded Knowledge answer and no customer data → passed through (no extra LLM call).
* Otherwise MiniMax combines customer findings + knowledge/web answers. Numbers must be grounded in the
  tool data / evidence / web results; one stricter regeneration, then a deterministic composition.
* Citations: only evidence actually given to a generator and referenced by the final answer.
"""

from __future__ import annotations

import re
import time
from typing import Any

from langgraph.runtime import Runtime

from app.agents.formatting import describe_all, message, value_dump
from app.agents.prompts import render_messages, tool_block
from app.errors import ProviderError
from app.guardrails.output import unsupported_numbers
from app.llm.base import strip_reasoning
from app.orchestration.routing import is_vague
from app.orchestration.runtime import GraphContext, event, node
from app.orchestration.state import AgentState, Citation

_CITE = re.compile(r"\[(E\d+)\]")


def _grounding(state: AgentState) -> str:
    parts = [state.get("message", "")]
    parts += [value_dump(t.get("data"), 8000) for t in state.get("tool_results") or [] if t.get("data")]
    parts += [describe_all(list(state.get("tool_results") or []), lang) for lang in ("pt", "en")]
    parts += [e.get("text", "") for e in state.get("retrieved_documents") or []]
    parts += [w.get("content", "") for w in state.get("web_results") or []]
    parts += [a.get("answer", "") for a in state.get("knowledge_answers") or []]
    # What a human operator told the customer is evidence too (numbers in it may be repeated).
    parts.append((state.get("slots") or {}).get("last_human_reply") or "")
    parts += [h.get("content", "") for h in state.get("history") or [] if h.get("role") == "human_operator"]
    return "\n".join(parts)


def compose_fallback(state: AgentState, lang: str) -> str:
    parts = []
    data = describe_all(list(state.get("tool_results") or []), lang)
    if data:
        parts.append(data)
    for a in state.get("knowledge_answers") or []:
        if a.get("answer"):
            parts.append(a["answer"])
    reply = (state.get("slots") or {}).get("last_human_reply")
    if reply:
        parts.append(
            ("Última resposta do especialista: " if lang == "pt" else "Latest reply from the specialist: ") + reply
        )
    return "\n\n".join(parts) or message("no_evidence", lang)


def final_citations(state: AgentState, answer: str) -> list[Citation]:
    referenced = set(_CITE.findall(answer))
    used_by_knowledge = {
        e
        for a in state.get("knowledge_answers") or []
        if a.get("status") in ("answered", "insufficient")
        for e in a.get("used_evidence") or []
    }
    keep = referenced or used_by_knowledge
    out: list[Citation] = []
    seen: set[str] = set()
    for e in state.get("retrieved_documents") or []:
        if e["evidence_id"] in keep and e["url"] not in seen:
            seen.add(e["url"])
            out.append(
                Citation(
                    title=e["title"],
                    url=e["url"],
                    category=e["category"],
                    retrieved_at=e.get("retrieved_at"),
                    evidence_id=e["evidence_id"],
                )
            )
    for w in (state.get("web_results") or [])[:3]:
        if (
            # "insufficient" web answers (e.g. sources disagree on a rate) still quote these results.
            any(
                a.get("mode") == "web" and a.get("status") in ("answered", "insufficient")
                for a in state.get("knowledge_answers") or []
            )
            and w["url"] not in seen
        ):
            seen.add(w["url"])
            out.append(
                Citation(
                    title=w["title"], url=w["url"], category="web", retrieved_at=w.get("retrieved_at"), evidence_id=""
                )
            )
    return out


@node("synthesizer")
async def synthesizer_node(state: AgentState, *, runtime: Runtime[GraphContext]) -> dict[str, Any]:
    ctx = runtime.context
    lang = state.get("language", "pt")
    intent = state.get("intent", "")
    started = time.time()
    answers = list(state.get("knowledge_answers") or [])
    tools = [t for t in state.get("tool_results") or [] if t.get("status") in ("ok", "not_found")]

    if intent == "unsupported":
        reason = "clarify" if is_vague(state.get("message") or "") else "unsupported"
        return {
            "candidate_answer": message(reason, lang),
            "citations": [],
            "events": [event("synthesizer", "deterministic", {"reason": reason}, started)],
        }
    if intent == "human_support":
        return {
            "candidate_answer": "",
            "citations": [],
            "events": [event("synthesizer", "skipped", {"reason": "handoff"}, started)],
        }
    if not tools and len(answers) == 1 and answers[0].get("answer"):
        text = answers[0]["answer"]
        return {
            "candidate_answer": text,
            "citations": final_citations(state, text),
            "events": [
                event(
                    "synthesizer",
                    "pass_through",
                    {"mode": answers[0].get("mode"), "status": answers[0].get("status")},
                    started,
                )
            ],
        }

    slots = state.get("slots") or {}
    task = {
        "language": lang,
        "question": state.get("message"),
        "intent": intent,
        "customer_findings": [f.get("summary") for f in state.get("support_findings") or []],
        "customer_data_facts": describe_all(tools, lang),
        "knowledge_answers": [
            {"answer": a.get("answer"), "status": a.get("status"), "mode": a.get("mode")} for a in answers
        ],
        "proposed_actions": [
            {"tool": p.get("tool"), "status": "pending human approval"} for p in state.get("proposed_actions") or []
        ],
        "steps_already_given": bool(slots.get("troubleshooting_evidence_used"))
        and intent in ("technical_support", "customer_terminal"),
        "open_case_id": slots.get("open_case_id"),
        "last_human_reply": slots.get("last_human_reply"),
    }
    blocks = [tool_block([{"tool": t["tool"], "data": t.get("data")} for t in tools])] if tools else []
    msgs = render_messages("synthesizer", task, history=(state.get("history") or [])[-4:], blocks=blocks)
    grounding = _grounding(state)
    events = []
    try:
        resp = await ctx.llm.chat(task="synthesizer", messages=msgs, max_tokens=700, temperature=0.2)
        text = strip_reasoning(resp.content)
        bad = unsupported_numbers(text, grounding)
        if bad:
            events.append(event("synthesizer", "numeric_claims_unsupported", {"numbers": bad[:5]}))
            resp = await ctx.llm.chat(
                task="synthesizer",
                messages=[
                    *msgs,
                    {"role": "assistant", "content": text},
                    {
                        "role": "user",
                        "content": f"These numbers are not supported by the data: {bad[:5]}. Rewrite the reply using only numbers present in the task input.",
                    },
                ],
                max_tokens=700,
                temperature=0.1,
            )
            text = strip_reasoning(resp.content)
            if unsupported_numbers(text, grounding):
                text = compose_fallback(state, lang)
                events.append(event("synthesizer", "deterministic_fallback", {"reason": "unsupported_numbers"}))
                return {
                    "candidate_answer": text,
                    "citations": final_citations(state, text),
                    "signals": ["unsupported_numeric_claim"],
                    "events": [*events, event("synthesizer", "composed", {"source": "fallback"}, started)],
                }
        source = "llm"
    except ProviderError as exc:
        text = compose_fallback(state, lang)
        source = "fallback"
        events.append(event("synthesizer", "llm_failed", {"error": exc.code}))
        return {
            "candidate_answer": text,
            "citations": final_citations(state, text),
            "errors": ["synthesizer:llm_failure"],
            "events": [*events, event("synthesizer", "composed", {"source": source}, started)],
        }
    return {
        "candidate_answer": text,
        "citations": final_citations(state, text),
        "events": [*events, event("synthesizer", "composed", {"source": source, "chars": len(text)}, started)],
    }
