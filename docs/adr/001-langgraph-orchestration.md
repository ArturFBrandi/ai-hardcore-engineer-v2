# ADR 001 — LangGraph as the orchestration runtime

**Status:** accepted · 2026-10-19

## Context
The challenge requires ≥3 cooperating agent types with an explicit communication mechanism, parallel work where
independent, a supervisor that reacts to results, and a human-in-the-loop that can pause a run and resume it later
(possibly on another replica, after restarts).

## Decision
Use **LangGraph** (`langgraph` 1.2) `StateGraph` with a typed `AgentState` (`app/orchestration/state.py`). Agents are
graph nodes; communication is the shared state with reducers; parallelism is nodes in the same superstep; HITL is
`interrupt()` + `Command(resume=…)` persisted by `langgraph-checkpoint-postgres`. Dependencies (LLM, tools, retriever,
`AuthContext`) are passed through LangGraph's runtime **context** (never checkpointed).

## Alternatives considered
- *Hand-written async orchestration*: simpler to read, but checkpoint/resume, superstep semantics and interrupt
  persistence would be re-implemented.
- *LangChain agents / CrewAI-style frameworks*: hide control flow behind LLM decisions, contradicting
  "the LLM proposes, code disposes".

## Consequences
- Real graph with conditional edges, fan-out and bounded loops; behaviour is testable with a fake LLM.
- Per-turn list channels needed a resettable reducer (`turn_list`): verified that `operator.add` accumulates across runs on
  one thread (spike, 2026-10-19).
- Verified (spike) that **new input on an interrupted thread drops the pending interrupt** while `Command(resume)` with the
  stored interrupt `checkpoint_id` still resumes the same thread → the case row stores that checkpoint id (ADR 008).

## Measured evidence
- `tests/orchestration/test_workflows.py::test_workflow1_settlement_specialists_run_in_parallel`: Customer Support and
  Knowledge intervals overlap (same superstep), asserted from wall-clock event timestamps.
- `test_workflow2_supervisor_dependency_with_product_filter`: Knowledge starts after Customer Support ends; filter
  `{"product": "Get Smart", "category": "terminal_troubleshooting"}`.
- Property test `tests/unit/test_components.py::test_supervisor_always_terminates_within_budget` (150 random cases).
