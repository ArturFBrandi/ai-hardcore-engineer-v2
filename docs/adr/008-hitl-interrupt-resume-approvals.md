# ADR 008 — Human-in-the-loop via interrupt/resume, approval gates and a transactional outbox

**Status:** accepted · 2026-10-19

## Decision
- The Escalation agent creates the case (`create_support_case`: idempotent unique key, pending action, outbox row and audit
  in one transaction), the handoff answer is saved, then `await_human` calls `interrupt(payload)`.
- The service stores the interrupt **checkpoint id** on the case. Operator `reply`/`decision` resumes the same thread
  from that checkpoint with `Command(resume=OperatorDecision)` → `execute_action` (approvals) → `human_reply`
  (MiniMax rewrite grounded on the operator text, or verbatim) → output guard → `human_operator` turn → SSE event.
- Resume runs with the **case owner's** `AuthContext` built from the case row; repositories make another customer's
  terminal unreachable even if an operator edits arguments.
- Double submit: the `awaiting_human` check happens inside the conversation lock + row lock; duplicates return the stored
  outcome (`duplicate: true`).
- Action policy table (`app/orchestration/policy.py`): `auto_allowed | requires_approval | forbidden`; approval-gated
  tools run only through `ToolRegistry.execute_approved`.
- Worker: outbox delivery (Teams/Slack-compatible webhook or log sink) with backoff + dead letter; SLA timer bumps
  priority, re-notifies and posts a customer status note; injectable clock.

## Alternatives considered
Ticket-only escalation (no way back for the human answer); a separate case-workflow graph (extra thread semantics).

## Measured evidence
`tests/hitl` 10 passed (full loop with SSE, duplicate submit, approve/reject/edit-to-foreign-terminal, restart before
resume, customer turn while waiting, SLA bump with injected clock, outbox retry + dead letter, role separation);
eval HITL suite 7/7.
