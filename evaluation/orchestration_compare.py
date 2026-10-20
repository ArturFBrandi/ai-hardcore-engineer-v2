"""ADR 010 measurement: supervisor with bounded replanning vs. static fan-out (``--live`` uses MiniMax-M3).

Static fan-out = every planned specialist runs in the first superstep (dependencies stripped) and the supervisor may
not add steps. For each question we record latency, LLM calls, tokens, cost, supervisor steps and whether the
Knowledge retrieval used the terminal-model filter. Writes ``evaluation/results/orchestration_compare.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import uuid
from pathlib import Path
from typing import Any

from app.agents import router as router_module
from app.container import build_container
from app.llm.fake import FakeLLMProvider
from app.orchestration import planning
from app.tools.context import AuthContext
from evaluation.environment import prepare
from evaluation.run_eval import eval_settings

OUT = Path("evaluation/results/orchestration_compare.md")
QUESTIONS = [
    "My card machine won't connect to the internet, what should I do?",
    "Minha maquininha não conecta na internet, o que eu faço?",
    "Minha Get Smart está sem sinal desde cedo",
    "When will the money from yesterday's sales be deposited?",
    "Quando vai cair o dinheiro das vendas de ontem?",
    "Minha maquininha está mostrando um erro de transação negada.",
]


def _static(plan_fn: Any) -> Any:
    def wrapper(*a: Any, **kw: Any) -> Any:
        plan = plan_fn(*a, **kw)
        for s in plan.get("steps", []):
            s["depends_on"] = []
        return plan

    return wrapper


async def run(live: bool) -> list[dict[str, Any]]:
    settings = eval_settings(live)
    await prepare(settings)
    rows = []
    for mode in ("supervisor", "static"):
        c = await build_container(
            settings.model_copy(
                update={"supervisor_max_steps": 0 if mode == "static" else settings.supervisor_max_steps}
            ),
            llm=None if live else FakeLLMProvider(),
        )
        await asyncio.to_thread(c.models.warmup)
        orig_v, orig_d = router_module.validate_plan, router_module.deterministic_plan
        if mode == "static":
            router_module.validate_plan = _static(planning.validate_plan)  # type: ignore[assignment]
            router_module.deterministic_plan = _static(planning.deterministic_plan)  # type: ignore[assignment]
        try:
            for q in QUESTIONS:
                r = await c.chat.run_turn(
                    AuthContext(customer_id="cliente1988", subject="cliente1988"),
                    q,
                    None,
                    f"req_cmp_{uuid.uuid4().hex[:8]}",
                    uuid.uuid4().hex,
                )
                s = r.state
                filters = [
                    e["detail"].get("filters")
                    for e in s["events"]
                    if e["agent"] == "knowledge" and e["event"] == "retrieved"
                ]
                rows.append(
                    {
                        "mode": mode,
                        "question": q,
                        "intent": s.get("intent"),
                        "latency_s": round(r.latency_ms / 1000, 2),
                        "llm_calls": r.usage.llm_calls,
                        "tokens": r.usage.total_tokens,
                        "usd": r.usage.cost_usd,
                        "supervisor_steps": sum(
                            e["agent"] == "supervisor" and e["detail"].get("decision") == "continue"
                            for e in s["events"]
                        ),
                        "model_filter": any((f or {}).get("product") for f in filters),
                        "sources": len(s.get("citations") or []),
                        "status": s.get("status"),
                    }
                )
        finally:
            router_module.validate_plan, router_module.deterministic_plan = orig_v, orig_d
            await c.close()
    return rows


def render(rows: list[dict[str, Any]], live: bool) -> str:
    lines = [
        f"# Orchestration comparison ({'MiniMax-M3' if live else 'fake LLM'})",
        "",
        "| mode | question | intent | latency s | LLM calls | tokens | USD | supervisor steps | model filter | sources | status |",
        "|---|---|---|---:|---:|---:|---:|---:|---|---:|---|",
    ]
    lines += [
        f"| {r['mode']} | {r['question'][:45]} | {r['intent']} | {r['latency_s']} | {r['llm_calls']} | {r['tokens']} | {r['usd']} | {r['supervisor_steps']} | {r['model_filter']} | {r['sources']} | {r['status']} |"
        for r in rows
    ]
    lines += [
        "",
        "| mode | mean latency s | mean LLM calls | mean tokens | mean USD | troubleshooting answers with model filter |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for mode in ("supervisor", "static"):
        m = [r for r in rows if r["mode"] == mode]
        ts = [r for r in m if r["intent"] == "technical_support"]
        lines.append(
            f"| {mode} | {statistics.mean(r['latency_s'] for r in m):.2f} | {statistics.mean(r['llm_calls'] for r in m):.2f} | "
            f"{statistics.mean(r['tokens'] for r in m):.0f} | {statistics.mean(r['usd'] for r in m):.5f} | {sum(r['model_filter'] for r in ts)}/{len(ts)} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true")
    a = p.parse_args()
    rows = asyncio.run(run(a.live))
    OUT.write_text(render(rows, a.live))
    print(OUT.read_text())


if __name__ == "__main__":
    main()
