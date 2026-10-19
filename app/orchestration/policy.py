"""Action policy table — code, not prompt.

Classifies every action an agent can request as ``auto_allowed``, ``requires_approval`` or ``forbidden``,
and declares which agent may hold which tool (least privilege). The tool registry consults this table
on every invocation; prompts merely describe it.
"""

from __future__ import annotations

from types import MappingProxyType

from app.db.models import ActionPolicy

ACTION_POLICY: MappingProxyType[str, ActionPolicy] = MappingProxyType(
    {
        # read-only customer data
        "get_customer_profile": ActionPolicy.AUTO_ALLOWED,
        "get_customer_terminals": ActionPolicy.AUTO_ALLOWED,
        "get_terminal_status": ActionPolicy.AUTO_ALLOWED,
        "get_recent_transactions": ActionPolicy.AUTO_ALLOWED,
        "get_transaction_details": ActionPolicy.AUTO_ALLOWED,
        "get_settlement_schedule": ActionPolicy.AUTO_ALLOWED,
        "get_open_support_cases": ActionPolicy.AUTO_ALLOWED,
        # writes
        "create_support_case": ActionPolicy.AUTO_ALLOWED,  # escalation agent only; idempotent
        "request_terminal_replacement": ActionPolicy.REQUIRES_APPROVAL,
        # never allowed, whatever the wording of the request
        "refund_transaction": ActionPolicy.FORBIDDEN,
        "change_bank_account": ActionPolicy.FORBIDDEN,
        "export_customer_data": ActionPolicy.FORBIDDEN,
    }
)

READ_TOOLS: frozenset[str] = frozenset(
    {
        "get_customer_profile",
        "get_customer_terminals",
        "get_terminal_status",
        "get_recent_transactions",
        "get_transaction_details",
        "get_settlement_schedule",
        "get_open_support_cases",
    }
)

# Least-privilege matrix: which tools each agent may hold.
AGENT_TOOLS: MappingProxyType[str, frozenset[str]] = MappingProxyType(
    {
        "router": frozenset(),
        "supervisor": frozenset(),
        "knowledge": frozenset({"rag_search", "web_search"}),
        "customer_support": READ_TOOLS | {"request_terminal_replacement"},
        "escalation": frozenset({"create_support_case"}),
        "synthesizer": frozenset(),
    }
)


def policy_for(action: str) -> ActionPolicy:
    """Unknown actions are forbidden by default."""
    return ACTION_POLICY.get(action, ActionPolicy.FORBIDDEN)


def agent_may_use(agent: str, tool: str) -> bool:
    return tool in AGENT_TOOLS.get(agent, frozenset())
