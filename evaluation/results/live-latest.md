# Evaluation report — live

- Date: 2026-10-20T03:10:11+00:00  ·  Commit: `301c4f7`  ·  LLM: MiniMax-M3
- Strict gate: PASS

| Metric | Value | Δ vs baseline | Threshold |
|---|---:|---:|---|
| single_turn_cases | 126 |  |  |
| case_pass_rate | 0.9841 |  | min 0.75 |
| intent_accuracy | 0.9818 |  | min 0.8 |
| routing_accuracy | 1.0 |  |  |
| tool_selection_accuracy | 1.0 |  |  |
| escalation_accuracy | 1.0 |  |  |
| guardrail_block_accuracy | 1.0 |  | min 1.0 |
| false_block_rate | 0.0 |  | max 0.05 |
| citation_presence | 1.0 |  |  |
| citation_faithfulness | 1.0 |  | min 0.95 |
| claim_recall | 0.8799 |  | min 0.6 |
| hallucination_rate | 0.0 |  | max 0.05 |
| answer_language_correctness | 0.9811 |  | min 0.9 |
| priority_accuracy | 1.0 |  |  |
| latency_p50_ms | 7996.6 |  |  |
| latency_p95_ms | 42386.7 |  |  |
| llm_calls_per_case | 2.888 |  |  |
| tokens_per_case | 6363.192 |  |  |
| cost_usd_per_case | 0.0023 |  |  |
| multiturn_conversations | 17 |  |  |
| multiturn_pass_rate | 0.8824 |  |  |
| context_carryover_accuracy | 0.9444 |  |  |
| hitl_scenarios | 7 |  |  |
| hitl_flow_success | 1.0 |  | min 0.85 |
| supervisor_scenarios | 6 |  |  |
| supervisor_step_accuracy | 1.0 |  |  |
| retrieval_questions | 66 |  |  |
| retrieval_recall@5 | 0.8939 |  |  |
| retrieval_mrr@10 | 0.846 |  |  |
| retrieval_ndcg@5 | 0.452 |  |  |
| retrieval_latency_p50_ms | 1125.8 |  |  |
| retrieval_latency_p95_ms | 1339.8 |  |  |

## Pass rate per tag

| Tag | Pass rate |
|---|---:|
| access | 1.0 |
| adversarial | 1.0 |
| ambiguous | 1.0 |
| auth | 1.0 |
| benign | 1.0 |
| challenge | 0.95 |
| customer_data | 1.0 |
| en | 1.0 |
| escalation | 1.0 |
| hallucination_trap | 1.0 |
| injection | 1.0 |
| knowledge | 0.9815 |
| missing_data | 1.0 |
| pt | 0.9672 |
| security | 1.0 |
| troubleshooting | 1.0 |
| unsupported | 1.0 |
| web | 1.0 |

## Failed single-turn cases

- `challenge-pix_bank-pt` — failed checks: intent (intent=customer_settlement, status=answered)
- `knowledge-g060` — failed checks: intent (intent=general_information, status=answered)
