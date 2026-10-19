# Threat model

## Assets
Merchant financial data (sales, settlements, bank domicile — synthetic here), conversation memory, support cases and
operator decisions, approval-gated actions (service orders), system prompts/canary, API keys (MiniMax, Tavily), JWT
signing keys, the knowledge corpus' integrity, operator time.

## Trust boundaries
Customer ↔ API · Operator ↔ API · API ↔ MiniMax (LLM provider sees prompts: minimized, redacted) · API ↔ Tavily ·
crawler ↔ public web · memory (past turns are untrusted) · worker ↔ webhook.

## STRIDE

| Threat | Example | Control | Test |
|---|---|---|---|
| **S**poofing | body `user_id=cliente2077` with another customer's token; forged/expired JWT | identity only from verified `sub`; mismatch → 403; JWKS verification, `alg` allow-list, `kid` rotation | `tests/security/test_security.py::test_invalid_tokens_rejected`, `test_jwt_body_mismatch_is_403_and_counted` |
| **T**ampering | client-sent history or thread id; editing approval args to another merchant's terminal | thread id built server-side; history loaded from DB; owner AuthContext on resume; append-only audit trigger | `test_foreign_conversation_is_generic_404`, `test_approval_edit_cannot_target_another_customers_terminal`, `test_audit_log_is_append_only` |
| **R**epudiation | operator denies approving | audit rows for case created/interrupt/claim/resume/reply/approval/execution with actor + request id | `tests/hitl/test_hitl.py` audit assertions |
| **I**nformation disclosure | cross-customer reads, prompt extraction, PII in logs/memory, canary leak | repository scoping, generic 404, input guardrails, redaction (logs, memory, traces, output), canary block | `test_cross_customer_and_bulk_access_blocked`, `test_tool_manipulation_by_llm_is_still_scoped`, `test_sensitive_data_redacted_in_memory_and_logs`, `test_canary_leak_is_blocked` |
| **D**enial of service | flooding, huge bodies, runaway loops, cost spikes | Redis sliding-window limits (IP, subject, operator writes), 16 KB body limit, request timeout, token budget, supervisor/tool-round caps, breakers | `test_rate_limit_per_subject`, `test_body_size_limit`, `test_supervisor_always_terminates_within_budget` |
| **E**levation of privilege | customer token on operator routes; LLM calling write tools | scope dependencies on every route (enumerated by a test), least-privilege tool matrix, approval gate | `test_every_route_declares_an_auth_dependency`, `test_least_privilege_matrix`, `test_approval_gated_action_not_executed_without_approval` |

## OWASP Top 10 for LLM Applications (2025)

| # | Risk | Control | Test file |
|---|---|---|---|
| LLM01 | Prompt injection (direct, encoded, multi-turn, indirect via RAG/web) | NFKC/confusable/zero-width/base64/leet normalization + rules, multi-turn detection that must involve the current message, untrusted delimiters, prompt hardening, attack traffic never auto-opens cases | `tests/security/test_security.py`, `tests/multiturn/test_memory.py::test_split_injection_across_turns_is_detected` |
| LLM02 | Sensitive information disclosure | customer-scoped repositories, redaction before persistence/logging/output, sanitized operator context, minimal data to the LLM | `tests/security/test_security.py::test_sensitive_data_redacted_in_memory_and_logs` |
| LLM03 | Supply chain | uv lockfile, pinned image digests, checksum-verified models (`config/models.lock.json`), pip-audit, Trivy, SBOM | `make security`, CI `security` + `docker` jobs |
| LLM04 | Data and model poisoning | allow-listed official domains, deny patterns, robots.txt, evidence treated as data, versioned checksummed snapshot | `tests/unit/test_components.py::test_crawler_url_rules`, `test_injection_planted_in_retrieved_evidence_is_data` |
| LLM05 | Improper output handling | output guard (canary, internal markup, redaction), numeric-claim grounding with regeneration/fallback, plain-text answers | `test_hallucinated_numbers_are_not_delivered`, `test_canary_leak_is_blocked` |
| LLM06 | Excessive agency | least-privilege tool matrix, policy table (`forbidden` default), approval gate, bounded loops | `tests/integration/test_tools.py::test_least_privilege_matrix`, `tests/hitl/test_hitl.py` |
| LLM07 | System prompt leakage | extraction rules, canary per process, prompts never returned | `test_prompt_injection_and_extraction_blocked` |
| LLM08 | Vector and embedding weaknesses | metadata filters inside Qdrant, citation verification, calibrated thresholds, entity-mismatch signal | `tests/orchestration/test_workflows.py`, `evaluation/results/calibration.md` |
| LLM09 | Misinformation | RAG authoritative for Getnet facts, "no verified information" answers, hallucination traps in the golden set | eval `hallucination_rate`, `test_knowledge_gap_is_answered_honestly_without_case` |
| LLM10 | Unbounded consumption | per-request token budget, rate limits, timeouts, breaker, cost metrics + alert | `tests/unit/test_llm_provider.py::test_token_budget_enforced_before_call`, `docs/observability/alerts.yml` |

## Residual risks (honest)
- Rule-based injection detection can be evaded by novel phrasings; prompt hardening and structural controls (scoping,
  approvals, canary) are the real defence. Rules have a measured false-block rate of 0 on the benign set only.
- Dev identity provider and `/v1/dev/token` exist for the demo; refused/disabled in production by configuration checks.
- Prompts sent to MiniMax contain redacted merchant data; a production deployment needs a DPA with the provider.
- Rate-limit fail-open during a Redis outage trades abuse protection for availability (metric + log).
