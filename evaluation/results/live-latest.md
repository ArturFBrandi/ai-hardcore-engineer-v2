# Evaluation report — live

- Date: 2026-10-20T00:03:57+00:00  ·  Commit: `7cd9132`  ·  LLM: MiniMax-M3
- Strict gate: FAIL

| Metric | Value | Δ vs baseline | Threshold |
|---|---:|---:|---|
| single_turn_cases | 126 |  |  |
| case_pass_rate | 0.9683 |  | min 0.75 |
| intent_accuracy | 0.9818 |  | min 0.8 |
| routing_accuracy | 1.0 |  |  |
| tool_selection_accuracy | 1.0 |  |  |
| escalation_accuracy | 0.9909 |  |  |
| guardrail_block_accuracy | 1.0 |  | min 1.0 |
| false_block_rate | 0.0 |  | max 0.05 |
| citation_presence | 1.0 |  |  |
| citation_faithfulness | 1.0 |  | min 0.95 |
| claim_recall | 0.8971 |  | min 0.6 |
| hallucination_rate | 0.0 |  | max 0.05 |
| answer_language_correctness | 0.9906 |  | min 0.9 |
| priority_accuracy | 1.0 |  |  |
| latency_p50_ms | 7047.0 |  |  |
| latency_p95_ms | 30569.5 |  |  |
| llm_calls_per_case | 3.112 |  |  |
| tokens_per_case | 6166.832 |  |  |
| cost_usd_per_case | 0.0023 |  |  |
| multiturn_conversations | 17 |  |  |
| multiturn_pass_rate | 0.9412 |  |  |
| context_carryover_accuracy | 0.9474 |  |  |
| hitl_scenarios | 7 |  |  |
| hitl_flow_success | 0.7143 |  | min 0.85 |
| supervisor_scenarios | 6 |  |  |
| supervisor_step_accuracy | 0.8333 |  |  |
| retrieval_questions | 66 |  |  |
| retrieval_recall@5 | 0.8939 |  |  |
| retrieval_mrr@10 | 0.846 |  |  |
| retrieval_ndcg@5 | 0.4565 |  |  |
| retrieval_latency_p50_ms | 1151.1 |  |  |
| retrieval_latency_p95_ms | 1438.7 |  |  |

## Pass rate per tag

| Tag | Pass rate |
|---|---:|
| access | 1.0 |
| adversarial | 1.0 |
| ambiguous | 0.5 |
| auth | 1.0 |
| benign | 1.0 |
| challenge | 0.9 |
| customer_data | 1.0 |
| en | 1.0 |
| escalation | 1.0 |
| hallucination_trap | 1.0 |
| injection | 1.0 |
| knowledge | 1.0 |
| missing_data | 1.0 |
| pt | 0.9344 |
| security | 1.0 |
| troubleshooting | 0.8333 |
| unsupported | 1.0 |
| web | 1.0 |

## Failed single-turn cases

- `challenge-pix_bank-pt` — failed checks: intent (intent=customer_settlement, status=answered)
- `challenge-anticipation-pt` — failed checks: intent (intent=customer_settlement, status=answered)
- `troubleshooting-replace-pt` — failed checks: interrupt_kind (intent=technical_support, status=awaiting_human)
- `ambiguous-pt` — failed checks: escalation (intent=human_support, status=awaiting_human)

## Threshold failures

- hitl_flow_success=0.7143 < min 0.85
