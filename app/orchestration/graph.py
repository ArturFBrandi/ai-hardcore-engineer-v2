"""LangGraph state machine.

    START → load_memory → input_guard ─(blocked)──────────────────────────────┐
                               └→ router ─┬→ customer_support ┐                │
                                          └→ knowledge ───────┴→ supervisor ⟲  │  (bounded replanning)
                                                                   ↓           │
                                                              synthesizer      │
                                                                   ↓           │
                                                                decision ─→ escalation
                                                                   ↓             ↓
                                                              output_guard ←─────┘
                                                                   ↓
                                                              save_memory ─(awaiting_human)→ await_human: interrupt()
                                                                   ↓                         ↓ Command(resume=OperatorDecision)
                                                                  END              execute_action (approvals) → human_reply → END

Independent specialist steps of one superstep run in parallel; the supervisor runs once after each superstep.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.customer_support import customer_support_node
from app.agents.escalation import await_human_node, escalation_node, execute_action_node, human_reply_node
from app.agents.knowledge import knowledge_node
from app.agents.router import router_node
from app.agents.synthesizer import synthesizer_node
from app.orchestration.nodes import (
    decision_node,
    input_guard_node,
    load_memory_node,
    output_guard_node,
    save_memory_node,
    supervisor_node,
)
from app.orchestration.runtime import GraphContext
from app.orchestration.state import AgentState

SPECIALIST_NODES = ("customer_support", "knowledge")

type CompiledGraph = CompiledStateGraph[AgentState, GraphContext, Any, Any]


def _after_guard(state: AgentState) -> str:
    return "output_guard" if state.get("blocked") else "router"


def _fan_out(state: AgentState) -> list[str] | str:
    agents = sorted({s["agent"] for s in state.get("active_steps") or [] if s["agent"] in SPECIALIST_NODES})
    return agents or "synthesizer"


def _after_supervisor(state: AgentState) -> list[str] | str:
    decision = (state.get("supervisor_decision") or {}).get("decision")
    if decision == "escalate":
        return "synthesizer"
    if decision == "continue":
        return _fan_out(state)
    return "synthesizer"


def _after_decision(state: AgentState) -> str:
    return "escalation" if state.get("needs_escalation") else "output_guard"


def _after_save(state: AgentState) -> str:
    return "await_human" if state.get("status") == "awaiting_human" and state.get("case_id") else END


def _after_human(state: AgentState) -> str:
    dec = state.get("operator_decision") or {}
    return "execute_action" if dec.get("kind") == "approval" else "human_reply"


def build_graph(checkpointer: BaseCheckpointSaver[Any] | None = None) -> CompiledGraph:
    g: StateGraph[AgentState, GraphContext, Any, Any] = StateGraph(AgentState, context_schema=GraphContext)
    g.add_node("load_memory", load_memory_node)
    g.add_node("input_guard", input_guard_node)
    g.add_node("router", router_node)
    g.add_node("customer_support", customer_support_node)
    g.add_node("knowledge", knowledge_node)
    g.add_node("supervisor", supervisor_node)
    g.add_node("synthesizer", synthesizer_node)
    g.add_node("decision", decision_node)
    g.add_node("escalation", escalation_node)
    g.add_node("output_guard", output_guard_node)
    g.add_node("save_memory", save_memory_node)
    g.add_node("await_human", await_human_node)
    g.add_node("execute_action", execute_action_node)
    g.add_node("human_reply", human_reply_node)

    g.add_edge(START, "load_memory")
    g.add_edge("load_memory", "input_guard")
    g.add_conditional_edges("input_guard", _after_guard, ["router", "output_guard"])
    g.add_conditional_edges("router", _fan_out, ["customer_support", "knowledge", "synthesizer"])
    g.add_edge("customer_support", "supervisor")
    g.add_edge("knowledge", "supervisor")
    g.add_conditional_edges("supervisor", _after_supervisor, ["customer_support", "knowledge", "synthesizer"])
    g.add_edge("synthesizer", "decision")
    g.add_conditional_edges("decision", _after_decision, ["escalation", "output_guard"])
    g.add_edge("escalation", "output_guard")
    g.add_edge("output_guard", "save_memory")
    g.add_conditional_edges("save_memory", _after_save, ["await_human", END])
    g.add_conditional_edges("await_human", _after_human, ["execute_action", "human_reply"])
    g.add_edge("execute_action", "human_reply")
    g.add_edge("human_reply", END)
    return g.compile(checkpointer=checkpointer)


def thread_id(customer_id: str, conversation_id: str) -> str:
    """Built only by server code — never from client input."""
    return f"{customer_id}:{conversation_id}"
