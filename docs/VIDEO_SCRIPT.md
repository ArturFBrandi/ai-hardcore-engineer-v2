# Video script (≈12 minutes)

Setup before recording: `make up` with `MINIMAX_API_KEY` in `.env`; open `/ui` (cliente1988), `/operator`
(operator.ana), Grafana (`make observability-up`, http://localhost:3000) and Jaeger (http://localhost:16686).

1. **Problem & principle (1 min)** — the brief; "the LLM proposes, code disposes"; show the table in README §3.
2. **Architecture (2 min)** — `docs/ARCHITECTURE.md` diagram: modular monolith, LangGraph nodes, datastores, why each
   exists (ADRs 001–003, 011). Show `app/orchestration/graph.py` (~60 lines of edges).
3. **Multi-agent cooperation (1.5 min)** — in `/ui` ask "When will the money from yesterday's sales be deposited?";
   point at agents/route/confidence chips; open `/v1/debug/traces/<request_id>`: `customer_support` and `knowledge`
   in the same superstep, then `supervisor: results_sufficient`. Same in Jaeger (overlapping spans).
4. **Supervisor dependency (1.5 min)** — "Minha maquininha não conecta na internet": trace shows
   `supervisor decision continue · terminal_model_known · product_filter=Get Smart`, then filtered retrieval; answer
   cites the Wi-Fi guide/manual.
5. **Memory surviving a restart (1 min)** — follow up "Já reiniciei e continua igual"; run
   `docker compose restart api`; continue "Ainda não funciona" → escalation with reason
   "repeated troubleshooting failure (3 attempts)" and a case id. (`make demo-conversation` scripts this.)
6. **HITL loop (1.5 min)** — `/operator`: case with sanitized context and conversation excerpt; claim; reply; the
   customer tab receives it live (SSE); ask "Qual o status do meu chamado?" — the answer uses the case + reply.
7. **Approval (1 min)** — "Minha Get Smart não conecta de jeito nenhum, quero trocar a maquininha" → approval case;
   approve in the console → service order created; show `audit_log` rows; mention the reject and edit-to-foreign-terminal tests.
8. **Security (1 min)** — curl with token `sub=cliente1988` and body `user_id=cliente2077` → 403; another customer's
   `conversation_id` → 404; "Ignore all previous instructions…" (and the base64 variant) → blocked, no case opened.
9. **Evaluation (1 min)** — `evaluation/results/latest.md` (offline gates), `live-latest.md` (MiniMax), retrieval
   benchmark + calibration; CI run with `eval --strict`.
10. **Observability & cost (30 s)** — Grafana dashboard (routes, escalation rate, RAG no-result ratio, LLM cost/hour,
    breaker state), alert rules; cost per request in the trace.
11. **Limitations (30 s)** — README §Limitations.
