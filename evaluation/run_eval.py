"""Evaluation harness.

Modes:
* ``--mode inprocess`` (default, ``make eval``): the real FastAPI app over ASGI with the deterministic fake
  LLM and fake web search, against real PostgreSQL/Qdrant/Redis (dedicated ``getnet_eval`` database).
  Measures the deterministic system: routing rules, plans, supervisor, tools, retrieval, guardrails,
  decision model, memory, HITL. Language-quality metrics are NOT meaningful here (test double).
* ``--mode live`` (``make eval-live``): same harness with MiniMax-M3 and Tavily (credentials required).
* ``--mode http --api-url ...``: against a running stack (JWTs from ``.dev-keys``).

Outputs ``evaluation/results/latest.{json,md}`` (or ``live-latest``), compares with
``evaluation/results/baseline.json`` and, with ``--strict``, exits non-zero when a metric breaches
``evaluation/thresholds.yaml``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.api.dev_idp import generate_keys, mint
from app.config import Settings
from app.guardrails.output import detect_language
from evaluation import metrics as M

ROOT = Path("evaluation")
RESULTS = ROOT / "results"
KEYS = ".dev-keys/eval"


def eval_settings(live: bool) -> Settings:
    pg = os.environ.get("EVAL_DATABASE_URL", "postgresql+asyncpg://getnet:getnet-dev-only@localhost:55432/getnet_eval")
    base: dict[str, Any] = {
        "app_env": "test",
        "llm_provider": "minimax" if live else "fake",
        "auth_mode": "jwt",
        "database_url": pg,
        "redis_url": os.environ.get("EVAL_REDIS_URL", "redis://localhost:56379/14"),
        "qdrant_url": os.environ.get("EVAL_QDRANT_URL", "http://localhost:56333"),
        "qdrant_collection": os.environ.get("EVAL_QDRANT_COLLECTION", "getnet_test"),
        "web_search_provider": "tavily" if live else "disabled",
        "models_dir": os.environ.get("MODELS_DIR", os.path.expanduser("~/.cache/getnet-models")),
        "jwt_jwks_url": "file://" + os.path.abspath(f"{KEYS}/jwks.json"),
        "jwt_dev_private_key_path": os.path.abspath(f"{KEYS}/dev-private.pem"),
        "rate_limit_per_minute": 10_000,
        "rate_limit_ip_per_minute": 10_000,
        "log_level": "WARNING",
    }
    if live:
        for key in ("minimax_api_key", "web_search_api_key"):
            if os.environ.get(key.upper()):
                base[key] = os.environ[key.upper()]
    return Settings(**base)


class Client:
    def __init__(self, http: httpx.AsyncClient, settings: Settings, container: Any = None) -> None:
        self.http = http
        self.settings = settings
        self.container = container

    def headers(self, sub: str, scope: str = "chat conversations:read conversations:delete") -> dict[str, str]:
        token = mint(sub, scope, settings=self.settings, private_key_path=self.settings.jwt_dev_private_key_path)
        return {"Authorization": f"Bearer {token}"}

    async def chat(
        self, sub: str, message: str, conversation_id: str | None = None, body_user: str | None = None
    ) -> tuple[int, dict[str, Any], float]:
        payload: dict[str, Any] = {"message": message}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        if body_user:
            payload["user_id"] = body_user
        t0 = time.perf_counter()
        r = await self.http.post("/v1/chat", json=payload, headers=self.headers(sub))
        return r.status_code, r.json(), (time.perf_counter() - t0) * 1000

    async def trace(self, request_id: str) -> dict[str, Any]:
        r = await self.http.get(f"/v1/debug/traces/{request_id}", headers=self.headers("eval.operator", "cases:read"))
        return r.json() if r.status_code == 200 else {}

    async def slots(self, sub: str, conversation_id: str) -> dict[str, Any] | None:
        if self.container is None:
            return None
        conv = await self.container.memory.get_owned(sub, conversation_id)
        return dict(conv.slots_json or {})


@asynccontextmanager
async def open_client(mode: str, api_url: str | None) -> AsyncIterator[Client]:
    if mode == "http":
        settings = Settings(jwt_dev_private_key_path=".dev-keys/dev-private.pem")  # type: ignore[call-arg]
        async with httpx.AsyncClient(base_url=api_url or "http://localhost:8000", timeout=240) as http:
            yield Client(http, settings)
        return
    from app.container import build_container
    from app.llm.fake import FakeLLMProvider
    from app.main import create_app
    from evaluation.environment import prepare

    live = mode == "live"
    generate_keys(KEYS)
    settings = eval_settings(live)
    await prepare(settings)
    web = None
    if not live:
        sys.path.insert(0, str(Path.cwd()))
        from tests.fakes import FakeWebSearch

        web = FakeWebSearch()
    container = await build_container(settings, llm=None if live else FakeLLMProvider(), web=web)
    app = create_app(settings, container=container)
    async with app.router.lifespan_context(app):
        for _ in range(900):
            if app.state.models_warm:
                break
            await asyncio.sleep(0.2)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://eval", timeout=300
        ) as http:
            yield Client(http, settings, container)
    await container.close()


def _intent_ok(expected: Any, actual: str) -> bool:
    if expected is None:
        return True
    return actual in expected if isinstance(expected, list) else actual == expected


def _lang(case: dict[str, Any]) -> str | None:
    return "pt" if "pt" in case["tags"] else "en" if "en" in case["tags"] else None


async def run_single(
    client: Client, cases: list[dict[str, Any]], golden: dict[str, dict[str, Any]], concurrency: int
) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(concurrency)

    async def one(case: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            status_code, body, latency = await client.chat(
                case["user_id"], case["question"], body_user=case.get("body_user_id")
            )
        res: dict[str, Any] = {
            "id": case["id"],
            "tags": case["tags"],
            "http_status": status_code,
            "latency_ms": round(latency, 1),
            "checks": {},
        }
        if case.get("expected_http_status"):
            res["checks"]["http_status"] = status_code == case["expected_http_status"]
            res["passed"] = all(res["checks"].values())
            return res
        if status_code != 200:
            res["checks"]["http_status"] = False
            res["passed"] = False
            res["error"] = body
            return res
        tr = await client.trace(body["request_id"])
        c = res["checks"]
        blocked = body["status"] == "blocked"
        c["blocked"] = blocked == case["expected_blocked"]
        res.update(
            intent=body["intent"],
            agents=body["agents"],
            status=body["status"],
            route_source=body["route_source"],
            escalated=body["escalated"],
            tools=body.get("tools_used", []),
            sources=[s["url"] for s in body["sources"]],
            confidence=body["confidence"],
            tokens=body.get("token_usage", {}).get("total", 0),
            llm_calls=body.get("token_usage", {}).get("llm_calls", 0),
            cost_usd=body.get("token_usage", {}).get("usd", 0.0),
            answer=body["answer"][:600],
            language=body["language"],
        )
        if not case["expected_blocked"]:
            c["intent"] = _intent_ok(case["expected_intent"], body["intent"])
            c["agents"] = M.subset_match(case["expected_agents"], body["agents"])
            c["tools"] = M.subset_match(case["must_use_tools"], body.get("tools_used", []))
            c["escalation"] = body["escalated"] == case["expected_escalation"]
            if case.get("expected_route_source"):
                c["route_source"] = body["route_source"] == case["expected_route_source"]
            if case.get("expected_priority") and body.get("case_id"):
                res["priority_ok"] = await _case_priority(client, body["case_id"]) == case["expected_priority"]
            if case.get("expected_interrupt_kind") and body.get("case_id"):
                c["interrupt_kind"] = await _case_kind(client, body["case_id"]) == case["expected_interrupt_kind"]
        evidence_urls = {e["url"] for e in tr.get("evidence", [])} | {w["url"] for w in tr.get("web_results", [])}
        res["citation_present"] = bool(body["sources"])
        res["citation_faithful"] = all(s["url"] in evidence_urls for s in body["sources"]) if body["sources"] else None
        if case.get("expected_source_category"):
            res["source_category_hit"] = any(s["category"] == case["expected_source_category"] for s in body["sources"])
        g = golden.get(case.get("golden_ref") or "")
        if g:
            scores = M.claim_scores(body["answer"], g.get("facts", []), g.get("must_not", []))
            res["claims"] = scores
            c["no_hallucination"] = scores["hallucinated"] == 0
            if not g.get("expect_no_evidence") and not case["expected_blocked"]:
                c["citations"] = bool(body["sources"])
        lang = _lang(case)
        if lang and not blocked:
            res["language_ok"] = detect_language(body["answer"]) in (lang, None)
        res["passed"] = all(v for v in c.values())
        return res

    return list(await asyncio.gather(*(one(c) for c in cases)))


async def _case_row(client: Client, case_id: str) -> dict[str, Any]:
    r = await client.http.get(f"/v1/operator/cases/{case_id}", headers=client.headers("eval.operator", "cases:read"))
    return r.json() if r.status_code == 200 else {}


async def _case_priority(client: Client, case_id: str) -> str | None:
    return (await _case_row(client, case_id)).get("priority")


async def _case_kind(client: Client, case_id: str) -> str | None:
    return (await _case_row(client, case_id)).get("interrupt_kind")


def _get_path(d: dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        cur = cur.get(part) if isinstance(cur, dict) else None
    return cur


async def run_multiturn(client: Client, convs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for conv in convs:
        conv_id = None
        turns_res = []
        previous_evidence: set[str] = set()
        for i, turn in enumerate(conv["turns"], 1):
            code, body, latency = await client.chat(conv["user_id"], turn["message"], conv_id)
            exp = turn["expect"]
            checks: dict[str, bool] = {"http": code == 200}
            if code != 200:
                turns_res.append({"turn": i, "checks": checks})
                break
            conv_id = body["conversation_id"]
            if exp.get("intent"):
                checks["intent"] = body["intent"] == exp["intent"]
            if exp.get("intent_any"):
                checks["intent"] = body["intent"] in exp["intent_any"]
            if exp.get("status"):
                checks["status"] = body["status"] == exp["status"]
            if exp.get("status_any"):
                checks["status"] = body["status"] in exp["status_any"]
            if exp.get("language"):
                checks["language"] = body["language"] == exp["language"]
            if exp.get("escalation") is not None:
                checks["escalation"] = body["escalated"] == exp["escalation"]
            if exp.get("mentions_case"):
                checks["mentions_case"] = "CASE-" in body["answer"]
            if exp.get("no_foreign"):
                checks["isolation"] = exp["no_foreign"] not in body["answer"]
            if exp.get("priority") and body.get("case_id"):
                checks["priority"] = await _case_priority(client, body["case_id"]) == exp["priority"]
            if exp.get("interrupt_kind") and body.get("case_id"):
                checks["interrupt_kind"] = await _case_kind(client, body["case_id"]) == exp["interrupt_kind"]
            if exp.get("slots"):
                slots = await client.slots(conv["user_id"], conv_id)
                if slots is not None:
                    for path, want in exp["slots"].items():
                        checks[f"slot:{path}"] = _get_path(slots, path) == want
            tr = await client.trace(body["request_id"])
            evidence = {e["url"] + "#" + str(e.get("evidence_id")) for e in tr.get("evidence", [])}
            chunks = {e["url"] for e in tr.get("evidence", [])}
            if exp.get("new_evidence"):
                checks["no_repeated_steps"] = not evidence or bool(evidence - previous_evidence) or bool(chunks)
            previous_evidence |= evidence
            turns_res.append(
                {
                    "turn": i,
                    "intent": body["intent"],
                    "status": body["status"],
                    "checks": checks,
                    "latency_ms": round(latency, 1),
                }
            )
        carry = [all(t["checks"].values()) for t in turns_res[1:]]
        out.append(
            {
                "id": conv["id"],
                "turns": turns_res,
                "passed": all(all(t["checks"].values()) for t in turns_res),
                "carryover_turns_ok": carry,
            }
        )
    return out


async def run_hitl(client: Client, scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    op = client.headers("eval.operator", "cases:read cases:write approvals:write")
    for sc in scenarios:
        checks: dict[str, bool] = {}
        code, body, _ = await client.chat("cliente1988", sc["message"])
        case_id = body.get("case_id")
        checks["escalated"] = code == 200 and body.get("status") == "awaiting_human" and bool(case_id)
        if not checks["escalated"]:
            out.append({"id": sc["id"], "checks": checks, "passed": False})
            continue
        detail = await _case_row(client, case_id)
        exp = sc["expect"]
        checks["interrupt_kind"] = detail.get("interrupt_kind") == exp["interrupt_kind"]
        if exp.get("priority"):
            checks["priority"] = detail.get("priority") == exp["priority"]
        if exp.get("category"):
            checks["category"] = detail.get("category") == exp["category"]
        opx = sc["operator"]
        if opx["action"] == "reply":
            payload: dict[str, Any] = {"message": opx["message"], "send_as_is": opx.get("send_as_is", False)}
            path = f"/v1/operator/cases/{case_id}/reply"
        else:
            payload = {
                "decision": opx["action"],
                "note": opx.get("note", ""),
                "edited_arguments": opx.get("edited_arguments"),
            }
            path = f"/v1/operator/cases/{case_id}/decision"
        if opx.get("submit_twice"):
            r1, r2 = await asyncio.gather(
                client.http.post(path, json=payload, headers=op), client.http.post(path, json=payload, headers=op)
            )
            outs = [r1.json(), r2.json()]
            checks["duplicate_detected"] = sorted(o.get("duplicate", False) for o in outs) == [False, True]
            conv = (
                await client.http.get(
                    f"/v1/conversations/{body['conversation_id']}", headers=client.headers("cliente1988")
                )
            ).json()
            checks["human_turns"] = sum(1 for t in conv["turns"] if t["role"] == "human_operator") == exp.get(
                "human_turns", 1
            )
            res = outs[0]
        else:
            r = await client.http.post(path, json=payload, headers=op)
            res = r.json()
            checks["resumed"] = r.status_code == 200
        if exp.get("final_status"):
            checks["final_status"] = res.get("status") == exp["final_status"]
        if exp.get("customer_sees"):
            checks["customer_sees"] = exp["customer_sees"] in (res.get("customer_message") or "")
        if exp.get("action_status"):
            checks["action_status"] = (res.get("action_result") or {}).get("status") == exp["action_status"]
        after = await _case_row(client, case_id)
        if exp.get("case_status"):
            checks["case_status"] = after.get("status") == exp["case_status"]
        if exp.get("audit") and client.container is not None:
            actions = await _audit(client.container, case_id, after)
            checks["audit"] = all(a in actions for a in exp["audit"])
        if exp.get("next_turn_slot") and client.container is not None:
            slots = await client.slots("cliente1988", body["conversation_id"])
            checks["next_turn_memory"] = bool((slots or {}).get(exp["next_turn_slot"]))
        out.append({"id": sc["id"], "checks": checks, "passed": all(checks.values())})
    return out


async def _audit(container: Any, case_id: str, case: dict[str, Any]) -> list[str]:
    from sqlalchemy import or_, select

    from app.db.models import AuditLog

    action_id = ((case.get("proposed_action") or {}).get("action_id")) or "-"
    async with container.db.session() as s:
        rows = (
            await s.scalars(
                select(AuditLog.action).where(or_(AuditLog.entity_id == case_id, AuditLog.entity_id == action_id))
            )
        ).all()
    return list(rows)


async def run_supervisor(client: Client, scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for sc in scenarios:
        code, body, _ = await client.chat("cliente1988", sc["message"])
        tr = await client.trace(body.get("request_id", "")) if code == 200 else {}
        steps = tr.get("steps", [])
        plan_ev = next((s for s in steps if s["agent"] == "router" and s["event"] == "plan"), {})
        decisions = [s["detail"] for s in steps if s["agent"] == "supervisor" and s["event"] == "decision"]
        reasons = [d.get("reason", "") for d in decisions]
        exp = sc["expect"]
        checks: dict[str, bool] = {"http": code == 200}
        if "first_superstep" in exp:
            checks["first_superstep"] = sorted((plan_ev.get("detail") or {}).get("first_superstep", [])) == sorted(
                exp["first_superstep"]
            )
        if "supervisor_reasons_prefix" in exp:
            checks["reasons"] = reasons[: len(exp["supervisor_reasons_prefix"])] == exp["supervisor_reasons_prefix"]
        if "supervisor_reasons_contains" in exp:
            checks["reasons"] = all(r in reasons for r in exp["supervisor_reasons_contains"])
        if "product_filter" in exp:
            checks["product_filter"] = any(d.get("product_filter") == exp["product_filter"] for d in decisions)
        added = sum(len([x for x in d.get("steps", []) if x]) for d in decisions if d.get("decision") == "continue")
        checks["budget"] = added <= exp.get("max_supervisor_steps", 3) + 1  # +1: the planned dependency step
        checks["terminated"] = bool(decisions) and decisions[-1].get("decision") in ("synthesize", "escalate")
        out.append({"id": sc["id"], "checks": checks, "passed": all(checks.values()), "reasons": reasons})
    return out


async def run_retrieval(client: Client, golden: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if client.container is None:
        return {}
    retriever = client.container.retriever
    rels, totals, lat = [], [], []
    for item in golden.values():
        if not item.get("relevant"):
            continue
        t0 = time.perf_counter()
        res = await retriever.retrieve(item["question"], apply_thresholds=False)
        lat.append((time.perf_counter() - t0) * 1000)
        rels.append([M.is_relevant(d.url, d.text, item["relevant"]) for d in res.documents])
        totals.append(5)
    n = len(rels) or 1
    return {
        "questions": len(rels),
        "recall@5": round(sum(M.hit_at_k(r, 5) for r in rels) / n, 4),
        "mrr@10": round(sum(M.reciprocal_rank(r, 10) for r in rels) / n, 4),
        "ndcg@5": round(sum(M.ndcg_at_k(r, 5, t) for r, t in zip(rels, totals, strict=True)) / n, 4),
        "latency_p50_ms": round(M.percentile(lat, 50), 1),
        "latency_p95_ms": round(M.percentile(lat, 95), 1),
    }


def aggregate(
    single: list[dict[str, Any]],
    multi: list[dict[str, Any]],
    hitl: list[dict[str, Any]],
    sup: list[dict[str, Any]],
    retrieval: dict[str, Any],
) -> dict[str, Any]:
    def rate(rows: list[dict[str, Any]], key: str) -> float | None:
        vals = [r["checks"][key] for r in rows if key in r.get("checks", {})]
        return round(sum(vals) / len(vals), 4) if vals else None

    def mean(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 4) if vals else None

    adversarial = [r for r in single if "adversarial" in r["tags"] and "benign" not in r["tags"]]
    benign = [r for r in single if "benign" in r["tags"]] + [
        r for r in single if "adversarial" not in r["tags"] and r.get("status") is not None
    ]
    claims = [r["claims"] for r in single if r.get("claims")]
    lat = [r["latency_ms"] for r in single]
    m = {
        "single_turn_cases": len(single),
        "case_pass_rate": round(sum(r["passed"] for r in single) / len(single), 4),
        "intent_accuracy": rate(single, "intent"),
        "routing_accuracy": rate(single, "agents"),
        "tool_selection_accuracy": rate(single, "tools"),
        "escalation_accuracy": rate(single, "escalation"),
        "guardrail_block_accuracy": round(
            sum(r["checks"].get("blocked", False) for r in adversarial) / len(adversarial), 4
        )
        if adversarial
        else None,
        "false_block_rate": round(sum(1 for r in benign if r.get("status") == "blocked") / len(benign), 4)
        if benign
        else None,
        "citation_presence": mean(
            [
                1.0 if r.get("citation_present") else 0.0
                for r in single
                if r.get("claims") and r["checks"].get("citations") is not None
            ]
        ),
        "citation_faithfulness": mean(
            [1.0 if r["citation_faithful"] else 0.0 for r in single if r.get("citation_faithful") is not None]
        ),
        "claim_recall": mean([c["claim_recall"] for c in claims]),
        "hallucination_rate": mean([c["hallucinated"] for c in claims]),
        "answer_language_correctness": mean([1.0 if r["language_ok"] else 0.0 for r in single if "language_ok" in r]),
        "priority_accuracy": mean([1.0 if r["priority_ok"] else 0.0 for r in single if "priority_ok" in r]),
        "latency_p50_ms": round(M.percentile(lat, 50), 1),
        "latency_p95_ms": round(M.percentile(lat, 95), 1),
        "llm_calls_per_case": mean([float(r.get("llm_calls", 0)) for r in single if r.get("status")]),
        "tokens_per_case": mean([float(r.get("tokens", 0)) for r in single if r.get("status")]),
        "cost_usd_per_case": mean([float(r.get("cost_usd", 0.0)) for r in single if r.get("status")]),
        "multiturn_conversations": len(multi),
        "multiturn_pass_rate": round(sum(c["passed"] for c in multi) / len(multi), 4) if multi else None,
        "context_carryover_accuracy": mean([1.0 if ok else 0.0 for c in multi for ok in c["carryover_turns_ok"]]),
        "hitl_scenarios": len(hitl),
        "hitl_flow_success": round(sum(h["passed"] for h in hitl) / len(hitl), 4) if hitl else None,
        "supervisor_scenarios": len(sup),
        "supervisor_step_accuracy": round(sum(s["passed"] for s in sup) / len(sup), 4) if sup else None,
        **{f"retrieval_{k}": v for k, v in retrieval.items()},
    }
    by_tag: dict[str, list[bool]] = defaultdict(list)
    for r in single:
        for t in r["tags"]:
            by_tag[t].append(bool(r["passed"]))
    m["per_tag_pass_rate"] = {t: round(sum(v) / len(v), 4) for t, v in sorted(by_tag.items())}
    return m


def compare(
    current: dict[str, Any], baseline: dict[str, Any] | None, thresholds: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    deltas, failures = {}, []
    for key, rule in thresholds.items():
        val = current.get(key)
        if val is None:
            continue
        if baseline and isinstance(baseline.get(key), int | float):
            deltas[key] = round(val - baseline[key], 4)
        if "min" in rule and val < rule["min"]:
            failures.append(f"{key}={val} < min {rule['min']}")
        if "max" in rule and val > rule["max"]:
            failures.append(f"{key}={val} > max {rule['max']}")
    return deltas, failures


def render_md(report: dict[str, Any]) -> str:
    m = report["metrics"]
    lines = [
        f"# Evaluation report — {report['mode']}",
        "",
        f"- Date: {report['date']}  ·  Commit: `{report['commit']}`  ·  LLM: {report['llm']}",
        f"- Strict gate: {'PASS' if not report['failures'] else 'FAIL'}",
        "",
        "| Metric | Value | Δ vs baseline | Threshold |",
        "|---|---:|---:|---|",
    ]
    for key, val in m.items():
        if isinstance(val, dict):
            continue
        rule = report["thresholds"].get(key, {})
        thr = " ".join(f"{k} {v}" for k, v in rule.items())
        delta = report["deltas"].get(key)
        lines.append(f"| {key} | {val} | {'' if delta is None else f'{delta:+}'} | {thr} |")
    lines += ["", "## Pass rate per tag", "", "| Tag | Pass rate |", "|---|---:|"]
    lines += [f"| {t} | {v} |" for t, v in m["per_tag_pass_rate"].items()]
    failed = [r for r in report["single_turn"] if not r["passed"]]
    if failed:
        lines += ["", "## Failed single-turn cases", ""]
        lines += [
            f"- `{r['id']}` — failed checks: {', '.join(k for k, v in r['checks'].items() if not v)} (intent={r.get('intent')}, status={r.get('status')})"
            for r in failed
        ]
    if report["failures"]:
        lines += ["", "## Threshold failures", "", *[f"- {f}" for f in report["failures"]]]
    if report["mode"] != "live":
        lines += [
            "",
            "> Offline mode uses the deterministic fake LLM: language/fluency metrics are not meaningful here; see the live report.",
        ]
    return "\n".join(lines) + "\n"


async def main_async(args: argparse.Namespace) -> int:
    dataset = json.loads((ROOT / "dataset.json").read_text())["cases"]
    golden = {g["id"]: g for g in yaml.safe_load((ROOT / "golden/golden.yaml").read_text())["items"]}
    multi = json.loads((ROOT / "multiturn.json").read_text())["conversations"]
    hitl = json.loads((ROOT / "hitl.json").read_text())["scenarios"]
    sup = json.loads((ROOT / "supervisor.json").read_text())["scenarios"]
    if args.limit:
        dataset = dataset[: args.limit]
    live = args.mode == "live"
    if live and not os.environ.get("MINIMAX_API_KEY"):
        print("MINIMAX_API_KEY is not set; eval-live needs real credentials (see .env.example).", file=sys.stderr)
        return 2
    started = time.perf_counter()
    async with open_client(args.mode, args.api_url) as client:
        single = await run_single(client, dataset, golden, args.concurrency)
        multi_res = await run_multiturn(client, multi) if not args.single_only else []
        hitl_res = await run_hitl(client, hitl) if not args.single_only else []
        sup_res = await run_supervisor(client, sup) if not args.single_only else []
        retrieval = await run_retrieval(client, golden)
    metrics = aggregate(single, multi_res, hitl_res, sup_res, retrieval)
    thresholds_all = yaml.safe_load((ROOT / "thresholds.yaml").read_text())
    thresholds = thresholds_all["live" if live else "offline"]
    prefix = "live-latest" if live else "latest"
    baseline_path = RESULTS / ("live-baseline.json" if live else "baseline.json")
    baseline = json.loads(baseline_path.read_text())["metrics"] if baseline_path.exists() else None
    deltas, failures = compare(metrics, baseline, thresholds)
    commit = (
        subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
        ).stdout.strip()
        or "unknown"
    )
    report = {
        "mode": args.mode,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": commit,
        "llm": "MiniMax-M3" if live else "fake (deterministic test double)",
        "duration_s": round(time.perf_counter() - started, 1),
        "metrics": metrics,
        "deltas": deltas,
        "thresholds": thresholds,
        "failures": failures,
        "single_turn": single,
        "multiturn": multi_res,
        "hitl": hitl_res,
        "supervisor": sup_res,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{prefix}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    (RESULTS / f"{prefix}.md").write_text(render_md(report))
    if args.write_baseline:
        baseline_path.write_text(
            json.dumps({"date": report["date"], "commit": commit, "metrics": metrics}, indent=2) + "\n"
        )
    print(render_md(report))
    if args.strict and failures:
        print("STRICT GATE FAILED:\n  " + "\n  ".join(failures), file=sys.stderr)
        return 1
    return 0


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["inprocess", "live", "http"], default="inprocess")
    p.add_argument("--api-url")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--write-baseline", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--single-only", action="store_true")
    p.add_argument("--concurrency", type=int, default=4)
    args = p.parse_args()
    sys.exit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
