"""Shared LangGraph state and domain models.

State values are JSON-safe (TypedDicts of primitives) so checkpoints round-trip through
``AsyncPostgresSaver`` across restarts and replicas without custom serializers. Pydantic models in the
agents validate data at the boundaries.

Per-turn list channels use ``turn_list`` — an add-reducer that resets when an update starts with the
``RESET`` marker. Every new turn's input resets them, so a thread's checkpoint never accumulates
previous turns' tool results/evidence (conversation memory lives in PostgreSQL tables instead).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

RESET = "__reset__"

AgentName = Literal["customer_support", "knowledge"]
SPECIALISTS: tuple[AgentName, ...] = ("customer_support", "knowledge")

INTENTS = (
    "getnet_product_information",
    "getnet_policy",
    "getnet_how_to",
    "technical_support",
    "customer_settlement",
    "customer_transaction",
    "customer_terminal",
    "customer_account",
    "case_status",
    "weather",
    "exchange_rate",
    "general_information",
    "human_support",
    "unsupported",
)
Intent = Literal[
    "getnet_product_information",
    "getnet_policy",
    "getnet_how_to",
    "technical_support",
    "customer_settlement",
    "customer_transaction",
    "customer_terminal",
    "customer_account",
    "case_status",
    "weather",
    "exchange_rate",
    "general_information",
    "human_support",
    "unsupported",
]

Status = Literal["answered", "escalated", "awaiting_human", "human_replied", "blocked"]


def turn_list(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
    """Add-reducer with an explicit reset marker (``[RESET, *items]``)."""
    right = right or []
    if right and right[0] == RESET:
        return list(right[1:])
    return [*(left or []), *right]


class ConversationTurn(TypedDict, total=False):
    turn: int
    role: str
    content: str
    intent: str | None
    case_id: str | None


class ConversationSlots(TypedDict, total=False):
    active_terminal_id: int | None
    active_terminal_model: str | None
    last_transaction_ids: list[str]
    last_date_range: dict[str, str] | None
    open_case_id: str | None
    preferred_language: str | None
    troubleshooting_attempts: dict[str, int]
    troubleshooting_evidence_used: list[str]
    last_intent: str | None
    last_intent_family: str | None
    last_human_reply: str | None


class PlanStep(TypedDict, total=False):
    id: str
    agent: str
    objective: str
    inputs: dict[str, Any]
    depends_on: list[str]
    origin: str  # router | supervisor


class Plan(TypedDict, total=False):
    steps: list[PlanStep]
    source: str  # llm | deterministic
    rejected_reason: str | None


class ToolResultEntry(TypedDict, total=False):
    tool: str
    status: str
    data: dict[str, Any] | None
    error: str | None
    retryable: bool
    latency_ms: float
    arguments: dict[str, Any]
    step_id: str


class EvidenceEntry(TypedDict, total=False):
    evidence_id: str
    chunk_id: str
    title: str
    url: str
    heading: str
    category: str
    product: str | None
    products: list[str]
    retrieved_at: str | None
    rerank_score: float | None
    fusion_score: float
    text: str
    step_id: str


class WebResultEntry(TypedDict, total=False):
    title: str
    url: str
    content: str
    score: float | None
    retrieved_at: str
    step_id: str


class KnowledgeAnswer(TypedDict, total=False):
    step_id: str
    mode: str  # getnet | web
    status: str  # answered | no_evidence | unavailable | error
    answer: str
    used_evidence: list[str]
    query: str
    filters: dict[str, Any]
    top_rerank_score: float | None
    broadened: bool


class SupportFindings(TypedDict, total=False):
    step_id: str
    status: str  # ok | partial | failed
    summary: str
    tools_called: list[str]


class ProposedAction(TypedDict, total=False):
    tool: str
    arguments: dict[str, Any]
    policy: str
    reason: str


class Citation(TypedDict, total=False):
    title: str
    url: str
    category: str
    retrieved_at: str | None
    evidence_id: str


class AgentEvent(TypedDict, total=False):
    agent: str
    event: str
    detail: dict[str, Any]
    started_at: str
    ended_at: str
    latency_ms: float


class GuardrailFlag(TypedDict, total=False):
    layer: str
    rule: str
    severity: str  # info | warn | block
    detail: str


class AgentState(TypedDict, total=False):
    # identity & request (set by server code only)
    request_id: str
    trace_id: str
    customer_id: str
    conversation_id: str
    turn: int
    message: str
    language: str

    # memory (untrusted content, windowed + redacted)
    history: list[ConversationTurn]
    summary: dict[str, Any] | None
    slots: ConversationSlots

    # routing & planning
    intent: str
    route: list[str]
    route_source: str
    route_flags: dict[str, Any]
    plan: Plan
    active_steps: list[PlanStep]
    completed_steps: Annotated[list[str], turn_list]
    supervisor_steps: int
    agent_visits: dict[str, int]
    supervisor_decision: dict[str, Any]

    # specialist outputs (parallel writers → reducers)
    retrieved_documents: Annotated[list[EvidenceEntry], turn_list]
    web_results: Annotated[list[WebResultEntry], turn_list]
    tool_results: Annotated[list[ToolResultEntry], turn_list]
    knowledge_answers: Annotated[list[KnowledgeAnswer], turn_list]
    support_findings: Annotated[list[SupportFindings], turn_list]
    proposed_actions: Annotated[list[ProposedAction], turn_list]

    # answer
    candidate_answer: str
    final_answer: str
    citations: Annotated[list[Citation], turn_list]

    # decision & escalation
    guardrail_flags: Annotated[list[GuardrailFlag], turn_list]
    signals: Annotated[list[str], turn_list]
    confidence: float
    escalation_reasons: Annotated[list[str], turn_list]
    needs_escalation: bool
    priority: str | None
    case_id: str | None
    case_category: str | None
    pending_action_id: str | None
    interrupt_kind: str | None
    status: Status
    blocked: bool

    # HITL resume
    operator_decision: dict[str, Any] | None
    action_result: dict[str, Any] | None
    human_message: str | None

    # accounting
    token_usage: dict[str, Any]
    errors: Annotated[list[str], turn_list]
    events: Annotated[list[AgentEvent], turn_list]


def reset_channels() -> dict[str, list[Any]]:
    """Input fragment that clears every per-turn list channel at the start of a run."""
    return {
        name: [RESET]
        for name in (
            "completed_steps",
            "retrieved_documents",
            "web_results",
            "tool_results",
            "knowledge_answers",
            "support_findings",
            "proposed_actions",
            "citations",
            "guardrail_flags",
            "signals",
            "escalation_reasons",
            "errors",
            "events",
        )
    }
