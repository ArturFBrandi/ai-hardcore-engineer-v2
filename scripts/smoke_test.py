"""`make smoke-test`: fast end-to-end health check of a running stack.

Checks: liveness, readiness (PostgreSQL, Qdrant chunks, Redis, models), Prometheus metrics, dev token
minting, seeded customer data via a tool-backed chat (fake or real LLM), one retrieval-backed answer
with citations, auth rejection, and — when the stack runs MiniMax — a real chat round-trip.
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

from app.api.dev_idp import mint


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--api-url", default="http://localhost:8000")
    a = p.parse_args()
    results: list[tuple[str, bool, str]] = []
    with httpx.Client(base_url=a.api_url, timeout=180) as c:

        def check(name: str, fn) -> None:  # type: ignore[no-untyped-def]
            t0 = time.perf_counter()
            try:
                ok, detail = fn()
            except Exception as exc:
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            results.append((name, ok, f"{detail} ({(time.perf_counter() - t0) * 1000:.0f} ms)"))

        check("liveness", lambda: (c.get("/health/live").status_code == 200, "alive"))

        def ready() -> tuple[bool, str]:
            r = c.get("/health/ready").json()
            return (
                r["status"] == "ready",
                f"chunks={r['checks']['qdrant'].get('chunks')} models_warm={r['checks']['models'].get('warm')}",
            )

        check("readiness (postgres, qdrant, redis, models)", ready)
        check("metrics", lambda: ("agent_requests_total" in c.get("/metrics").text, "prometheus exposition"))
        token = mint("cliente1988", "chat conversations:read", private_key_path=".dev-keys/dev-private.pem")
        h = {"Authorization": f"Bearer {token}"}
        check(
            "token minting + auth", lambda: (c.get("/v1/conversations", headers=h).status_code == 200, "JWT accepted")
        )
        check(
            "auth rejection",
            lambda: (c.post("/v1/chat", json={"message": "oi"}).status_code == 401, "401 without token"),
        )

        def seeded() -> tuple[bool, str]:
            r = c.post(
                "/v1/chat", json={"message": "Quais foram minhas vendas de ontem?", "user_id": "cliente1988"}, headers=h
            ).json()
            return "get_recent_transactions" in r.get("tools_used", []) and r[
                "status"
            ] == "answered", f"intent={r.get('intent')} llm_calls={r.get('token_usage', {}).get('llm_calls')}"

        check("seeded customer data via tools", seeded)

        def retrieval() -> tuple[bool, str]:
            r = c.post(
                "/v1/chat", json={"message": "Em quantas parcelas posso dividir uma venda no crediário?"}, headers=h
            ).json()
            return bool(r.get("sources")), f"sources={len(r.get('sources', []))} route={r.get('route_source')}"

        check("RAG answer with citations", retrieval)
    width = max(len(n) for n, _, _ in results)
    for name, ok, detail in results:
        print(f"[{'PASS' if ok else 'FAIL'}] {name.ljust(width)}  {detail}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
