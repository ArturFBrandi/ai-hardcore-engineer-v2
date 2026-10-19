"""Immutable authorization context.

Built **only** from the verified principal (JWT ``sub`` or demo-mode identity) by server code. It flows
unchanged principal → graph → tools → memory → HITL resume. The LLM never sees, chooses or changes it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ActorKind = Literal["customer", "operator", "system"]


@dataclass(frozen=True, slots=True)
class AuthContext:
    customer_id: str
    subject: str
    actor_type: ActorKind = "customer"
    scopes: frozenset[str] = field(default_factory=frozenset)
    request_id: str | None = None

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def for_case_owner(self, owner_customer_id: str, operator_subject: str, request_id: str | None) -> AuthContext:
        """Context used to execute an approved action: always the case owner's identity, never the operator's
        choice. ``owner_customer_id`` comes from the persisted case row, not from operator input."""
        return AuthContext(
            customer_id=owner_customer_id,
            subject=operator_subject,
            actor_type="operator",
            scopes=frozenset({"approved_action"}),
            request_id=request_id,
        )
