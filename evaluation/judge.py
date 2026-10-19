"""Optional LLM judge (probabilistic) and its calibration against labeled answers (``make judge-calibrate``).

The judge (MiniMax-M3, temperature 0, separate prompt and call from generation) only answers semantic
questions that deterministic checks cannot: is the answer grounded in and consistent with the reference
facts? It is calibrated on ``golden/judge_calibration.yaml`` (30 labeled answers); accuracy and Cohen's κ are
reported in ``results/judge_calibration.md``. It is NEVER a CI gate; if agreement is weak the report says so.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import yaml
from pydantic import BaseModel

from app.agents.prompts import render_messages
from app.config import Settings
from app.errors import ProviderError
from app.llm.base import LLMProvider, structured
from app.llm.minimax import MiniMaxProvider
from evaluation.metrics import cohen_kappa

OUT = Path("evaluation/results/judge_calibration.md")


class Verdict(BaseModel):
    grounded: bool
    complete: bool = False
    reason: str = ""


async def judge(llm: LLMProvider, question: str, facts: list[str], answer: str) -> Verdict:
    msgs = render_messages("judge", {"question": question, "reference_facts": facts, "candidate_answer": answer})
    out, _ = await structured(llm, task="judge", messages=msgs, schema=Verdict, max_tokens=200)
    return out


async def calibrate() -> int:
    if not os.environ.get("MINIMAX_API_KEY"):
        print("MINIMAX_API_KEY not set — judge calibration needs the real model.", file=sys.stderr)
        return 2
    llm = MiniMaxProvider(Settings(minimax_api_key=os.environ["MINIMAX_API_KEY"], llm_temperature=0.0))  # type: ignore[arg-type]
    golden = {g["id"]: g for g in yaml.safe_load(Path("evaluation/golden/golden.yaml").read_text())["items"]}
    cal = yaml.safe_load(Path("evaluation/golden/judge_calibration.yaml").read_text())
    human, machine, rows = [], [], []
    for item in cal["items"]:
        g = golden[item["golden_id"]]
        try:
            v = await judge(llm, g["question"], [f["text"] for f in g["facts"]], item["answer"])
            verdict: bool | None = v.grounded
        except ProviderError:
            verdict = None
        rows.append({**item, "judge": verdict})
        if verdict is not None:
            human.append(bool(item["human_label_correct"]))
            machine.append(verdict)
    n = len(human)
    acc = sum(1 for h, m in zip(human, machine, strict=True) if h == m) / n if n else 0.0
    kappa = cohen_kappa(human, machine)
    strength = "strong" if kappa >= 0.8 else "moderate" if kappa >= 0.6 else "weak"
    lines = [
        "# LLM judge calibration (probabilistic — not a CI gate)",
        "",
        f"Judge: MiniMax-M3, temperature 0, separate prompt (`app/agents/prompts/judge.md`). Items judged: {n}/{len(cal['items'])}.",
        f"Labels: {cal['notice']}",
        "",
        f"**Accuracy vs labels: {acc:.3f} · Cohen's κ: {kappa:.3f} ({strength} agreement).**",
        "",
        "Use: the judge may be consulted for semantic groundedness in exploratory live runs. Deterministic metrics "
        "(claim recall, hallucination traps, citation faithfulness) remain the gates" + (". Agreement is weak: do not rely on the judge." if strength == "weak" else "."),
        "",
        "| golden | label | judge | answer |",
        "|---|---|---|---|",
        *[f"| {r['golden_id']} | {r['human_label_correct']} | {r['judge']} | {r['answer'][:90]} |" for r in rows],
    ]
    OUT.write_text("\n".join(lines) + "\n")
    Path("evaluation/results/judge_calibration.json").write_text(json.dumps({"accuracy": acc, "kappa": kappa, "n": n, "rows": rows}, indent=2, ensure_ascii=False))
    print(f"judge accuracy={acc:.3f} kappa={kappa:.3f} ({strength})")
    return 0


def main() -> None:
    sys.exit(asyncio.run(calibrate()))


if __name__ == "__main__":
    main()


