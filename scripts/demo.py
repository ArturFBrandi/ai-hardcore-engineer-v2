"""Demo runner against a running stack.

make demo-scenarios      → the 10 challenge scenarios in PT and EN, one table row each
make demo-conversation   → scripted multi-turn: memory across an API restart, supervisor dependency,
                           repeated-failure escalation, operator reply via the API (CLI path), customer
                           receiving it, next turn using it
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import textwrap
import time
from typing import Any

import httpx

from app.api.dev_idp import mint

SCENARIOS = [
    (
        "What's the difference between the Get Clássica and the Get Smart?",
        "Qual a diferença entre a Get Clássica e a Get Smart?",
    ),
    ("What's the weather forecast in Porto Alegre tomorrow?", "Qual a previsão do tempo em Porto Alegre amanhã?"),
    ("When will the money from yesterday's sales be deposited?", "Quando vai cair o dinheiro das vendas de ontem?"),
    (
        "Do I need a bank account to receive my sales via Pix?",
        "Preciso de conta bancária para receber minhas vendas via Pix?",
    ),
    (
        "My card machine won't connect to the internet, what should I do?",
        "Minha maquininha não conecta na internet, o que eu faço?",
    ),
    (
        "How does receivables advance (antecipação) work with Getnet?",
        "Como funciona a antecipação de recebíveis na Getnet?",
    ),
    ("What's the euro exchange rate today?", "Qual a cotação do euro hoje?"),
    (
        "My card machine is showing a transaction decline error.",
        "Minha maquininha está mostrando um erro de transação negada.",
    ),
    (
        "How many installments can I split a sale into with the crediário?",
        "Em quantas parcelas posso dividir uma venda no crediário?",
    ),
    ("Can I sell through WhatsApp using the Payment Link?", "Posso vender pelo WhatsApp usando o Link de Pagamento?"),
]


def headers(sub: str, scope: str = "chat conversations:read conversations:delete") -> dict[str, str]:
    return {"Authorization": f"Bearer {mint(sub, scope, private_key_path='.dev-keys/dev-private.pem')}"}


def chat(c: httpx.Client, message: str, conv: str | None = None, sub: str = "cliente1988") -> dict[str, Any]:
    payload: dict[str, Any] = {"message": message, "user_id": sub}
    if conv:
        payload["conversation_id"] = conv
    t0 = time.perf_counter()
    r = c.post("/v1/chat", json=payload, headers=headers(sub))
    r.raise_for_status()
    body = r.json()
    body["_latency_s"] = round(time.perf_counter() - t0, 1)
    return body


def trace_steps(c: httpx.Client, request_id: str) -> list[str]:
    r = c.get(f"/v1/debug/traces/{request_id}", headers=headers("demo.operator", "cases:read"))
    if r.status_code != 200:
        return []
    return [
        f"{s['agent']}:{s['detail'].get('reason', '')}"
        for s in r.json().get("steps", [])
        if s["agent"] == "supervisor" and s["event"] == "decision"
    ]


def scenarios(c: httpx.Client) -> int:
    rows = []
    for en, pt in SCENARIOS:
        for q in (en, pt):
            b = chat(c, q)
            plan = ",".join(s.split(":")[1] for s in b.get("plan_steps", []))
            rows.append(
                (
                    q[:58],
                    b["intent"],
                    b["route_source"],
                    plan,
                    ",".join(b.get("tools_used", [])) or "-",
                    len(b["sources"]),
                    "yes" if b["escalated"] else "no",
                    b["_latency_s"],
                    b.get("token_usage", {}).get("llm_calls", "-"),
                    b.get("token_usage", {}).get("total", "-"),
                )
            )
    head = ("question", "intent", "route", "plan", "tools", "src", "esc", "lat_s", "llm", "tokens")
    widths = [max(len(str(r[i])) for r in [head, *rows]) for i in range(len(head))]
    for r in [head, *rows]:
        print("  ".join(str(v).ljust(w) for v, w in zip(r, widths, strict=True)))
    return 0


def say(label: str, body: dict[str, Any]) -> None:
    print(f"\n── {label}  [{body['intent']} · {body['route_source']} · {body['status']} · {body['_latency_s']}s]")
    print(textwrap.indent(textwrap.fill(body["answer"], 110, replace_whitespace=False), "   "))
    if body.get("case_id"):
        print(f"   case: {body['case_id']}  reasons: {body['escalation_reasons']}")


def conversation(c: httpx.Client, restart: bool) -> int:
    b1 = chat(c, "Minha Get Smart não conecta na internet")
    conv = b1["conversation_id"]
    say("turn 1 — troubleshooting (Customer Support → Supervisor → Knowledge with product filter)", b1)
    print("   supervisor:", trace_steps(c, b1["request_id"]))
    if restart:
        print("\n── restarting the api container between turns (memory lives in PostgreSQL) …")
        subprocess.run(["docker", "compose", "restart", "api"], check=True, capture_output=True)
        for _ in range(120):
            try:
                if c.get("/health/ready").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(1)
    b2 = chat(c, "Já reiniciei e continua igual", conv)
    say("turn 2 — follow-up after restart (attempt 2, new steps)", b2)
    b3 = chat(c, "Ainda não funciona, continua sem conexão", conv)
    say("turn 3 — repeated failure → escalation + interrupt()", b3)
    case_id = b3.get("case_id")
    if not case_id:
        print("no case created — check the decision model / escalation thresholds")
        return 1
    op = headers("operator.ana", "cases:read cases:write approvals:write")
    detail = c.get(f"/v1/operator/cases/{case_id}", headers=op).json()
    print(
        f"\n── operator sees {case_id}: priority={detail['priority']} category={detail['category']} kind={detail['interrupt_kind']}"
    )
    note = "Abrimos uma ordem de serviço para trocar o chip da sua Get Smart. Protocolo 55821; o técnico vai até você em até 2 dias úteis."
    if detail["interrupt_kind"] == "approval":
        # MiniMax may propose a replacement when diagnostics show a persistent fault: the operator decides it.
        resp = c.post(f"/v1/operator/cases/{case_id}/decision", json={"decision": "approve", "note": note}, headers=op)
        what = "operator approved the proposed action"
    else:
        resp = c.post(f"/v1/operator/cases/{case_id}/reply", json={"message": note, "send_as_is": False}, headers=op)
        what = "operator reply"
    if resp.status_code != 200:
        print(f"operator call failed: HTTP {resp.status_code} {resp.text[:300]}")
        return 1
    reply = resp.json()
    print(f"── {what} resumed the graph → status {reply['status']}")
    turns = c.get(f"/v1/conversations/{conv}", headers=headers("cliente1988")).json()["turns"]
    human = [t for t in turns if t["role"] == "human_operator"][-1]
    print("── customer receives (GET /v1/conversations/{id} and SSE):")
    print(textwrap.indent(textwrap.fill(human["content"], 110), "   "))
    b4 = chat(c, "Qual o status do meu chamado?", conv)
    say("turn 4 — next turn uses the case + human reply from memory", b4)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=["scenarios", "conversation"])
    p.add_argument("--api-url", default="http://localhost:8000")
    p.add_argument("--no-restart", action="store_true")
    a = p.parse_args()
    with httpx.Client(base_url=a.api_url, timeout=240) as c:
        return scenarios(c) if a.what == "scenarios" else conversation(c, restart=not a.no_restart)


if __name__ == "__main__":
    sys.exit(main())
