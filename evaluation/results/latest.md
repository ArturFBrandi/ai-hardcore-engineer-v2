# Evaluation report — inprocess

- Date: 2026-10-20T02:32:40+00:00  ·  Commit: `7cd9132`  ·  LLM: fake (deterministic test double)
- Strict gate: PASS

| Metric | Value | Δ vs baseline | Threshold |
|---|---:|---:|---|
| single_turn_cases | 126 |  |  |
| case_pass_rate | 0.9762 | +0.0 | min 0.95 |
| intent_accuracy | 0.9909 | +0.0 | min 0.97 |
| routing_accuracy | 0.9909 | +0.0 | min 0.97 |
| tool_selection_accuracy | 1.0 | +0.0 | min 0.98 |
| escalation_accuracy | 0.9909 | +0.0 | min 0.97 |
| guardrail_block_accuracy | 1.0 | +0.0 | min 1.0 |
| false_block_rate | 0.0 | +0.0 | max 0.02 |
| citation_presence | 0.9848 | +0.0 | min 0.95 |
| citation_faithfulness | 1.0 | +0.0 | min 1.0 |
| claim_recall | 0.8407 |  |  |
| hallucination_rate | 0.0 | +0.0 | max 0.0 |
| answer_language_correctness | 0.7264 |  |  |
| priority_accuracy | 1.0 |  |  |
| latency_p50_ms | 3290.4 |  |  |
| latency_p95_ms | 4176.7 |  |  |
| llm_calls_per_case | 2.408 |  |  |
| tokens_per_case | 3656.2 |  |  |
| cost_usd_per_case | 0.0014 |  |  |
| multiturn_conversations | 17 |  |  |
| multiturn_pass_rate | 1.0 | +0.0 | min 0.94 |
| context_carryover_accuracy | 1.0 |  |  |
| hitl_scenarios | 7 |  |  |
| hitl_flow_success | 1.0 | +0.0 | min 1.0 |
| supervisor_scenarios | 6 |  |  |
| supervisor_step_accuracy | 1.0 | +0.0 | min 1.0 |
| retrieval_questions | 66 |  |  |
| retrieval_recall@5 | 0.8939 | -0.0152 | min 0.88 |
| retrieval_mrr@10 | 0.846 | -0.005 | min 0.82 |
| retrieval_ndcg@5 | 0.452 |  |  |
| retrieval_latency_p50_ms | 1189.7 |  |  |
| retrieval_latency_p95_ms | 1426.9 |  |  |

## Pass rate per tag

| Tag | Pass rate |
|---|---:|
| access | 1.0 |
| adversarial | 1.0 |
| ambiguous | 1.0 |
| auth | 1.0 |
| benign | 1.0 |
| challenge | 1.0 |
| customer_data | 1.0 |
| en | 0.9556 |
| escalation | 1.0 |
| hallucination_trap | 1.0 |
| injection | 1.0 |
| knowledge | 0.9815 |
| missing_data | 1.0 |
| pt | 0.9836 |
| security | 1.0 |
| troubleshooting | 0.6667 |
| unsupported | 1.0 |
| web | 1.0 |

## Failed single-turn cases

- `knowledge-g035` — failed checks: citations (intent=getnet_product_information, status=answered)
- `troubleshooting-smart-offline-pt` — failed checks: escalation (intent=technical_support, status=awaiting_human)
- `troubleshooting-not-connecting-en` — failed checks: intent, agents (intent=getnet_product_information, status=answered)

> Offline mode uses the deterministic fake LLM: language/fluency metrics are not meaningful here; see the live report.
