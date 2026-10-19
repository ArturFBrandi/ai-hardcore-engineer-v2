"""SQLAlchemy ORM models.

Two groups of tables:

* **Synthetic support backend** (clearly labelled synthetic; masked documents/cards/bank data only):
  customers, terminals, terminal_events, transactions, settlements, support_cases, service_orders.
* **Platform**: conversations, conversation_turns, pending_actions, outbox, audit_log.

LangGraph checkpoint tables are created by ``AsyncPostgresSaver.setup()`` (run by the migrate job).
The schema is owned by Alembic — ``create_all()`` is never used as the schema strategy.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def _enum(e: type[StrEnum], name: str) -> Enum:
    return Enum(
        e,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda x: [m.value for m in x],
    )


# --------------------------------------------------------------------------------------- enums
class Segment(StrEnum):
    MEI = "mei"
    SMALL_BUSINESS = "small_business"
    MIDDLE_MARKET = "middle_market"


class TerminalStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    MAINTENANCE = "maintenance"
    BLOCKED = "blocked"


class Connectivity(StrEnum):
    WIFI = "wifi"
    G4 = "4g"
    WIFI_4G = "wifi+4g"


class TerminalEventType(StrEnum):
    CONNECTIVITY_LOSS = "connectivity_loss"
    CONNECTIVITY_RESTORED = "connectivity_restored"
    ERROR_CODE = "error_code"
    REBOOT = "reboot"
    FIRMWARE_UPDATE = "firmware_update"
    LOW_BATTERY = "low_battery"


class PaymentMethod(StrEnum):
    CREDIT = "credit"
    DEBIT = "debit"
    PIX = "pix"
    PAYMENT_LINK = "payment_link"
    CREDIARIO = "crediario"


class TransactionStatus(StrEnum):
    APPROVED = "approved"
    DECLINED = "declined"
    PENDING = "pending"
    REFUNDED = "refunded"


class SettlementStatus(StrEnum):
    COMPLETED = "completed"
    PENDING = "pending"
    SCHEDULED = "scheduled"


class CaseCategory(StrEnum):
    POS_CONNECTIVITY = "POS_CONNECTIVITY"
    POS_HARDWARE = "POS_HARDWARE"
    TRANSACTION_ISSUE = "TRANSACTION_ISSUE"
    SETTLEMENT_ISSUE = "SETTLEMENT_ISSUE"
    ACCOUNT_ISSUE = "ACCOUNT_ISSUE"
    PRODUCT_QUESTION = "PRODUCT_QUESTION"
    GENERAL_REQUEST = "GENERAL_REQUEST"
    SECURITY_CONCERN = "SECURITY_CONCERN"


class CasePriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class CaseStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    AWAITING_CUSTOMER = "awaiting_customer"
    RESOLVED = "resolved"
    CLOSED = "closed"


class InterruptKind(StrEnum):
    REPLY = "reply"
    APPROVAL = "approval"


class ActionStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


class ActionPolicy(StrEnum):
    AUTO_ALLOWED = "auto_allowed"
    REQUIRES_APPROVAL = "requires_approval"
    FORBIDDEN = "forbidden"


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    AWAITING_HUMAN = "awaiting_human"
    CLOSED = "closed"


class TurnRole(StrEnum):
    CUSTOMER = "customer"
    ASSISTANT = "assistant"
    HUMAN_OPERATOR = "human_operator"
    SYSTEM_NOTE = "system_note"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    DEAD = "dead"


class ActorType(StrEnum):
    CUSTOMER = "customer"
    AGENT = "agent"
    OPERATOR = "operator"
    SYSTEM = "system"


class ServiceOrderStatus(StrEnum):
    REQUESTED = "requested"
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


def _now() -> Any:
    return func.now()


# ------------------------------------------------------------------------- synthetic backend
class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    trade_name: Mapped[str] = mapped_column(String(200))
    document_masked: Mapped[str] = mapped_column(String(32))
    email_masked: Mapped[str] = mapped_column(String(200))
    segment: Mapped[Segment] = mapped_column(_enum(Segment, "segment"))
    plan: Mapped[str] = mapped_column(String(64))
    pix_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    anticipation_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    settlement_days: Mapped[int] = mapped_column(Integer)
    bank_masked: Mapped[str] = mapped_column(String(64))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    __table_args__ = (CheckConstraint("settlement_days BETWEEN 0 AND 60", name="settlement_days_range"),)


class Terminal(Base):
    __tablename__ = "terminals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    serial_number: Mapped[str] = mapped_column(String(32), unique=True)
    model: Mapped[str] = mapped_column(String(64))
    status: Mapped[TerminalStatus] = mapped_column(_enum(TerminalStatus, "terminal_status"))
    connectivity: Mapped[Connectivity] = mapped_column(_enum(Connectivity, "connectivity"))
    firmware_version: Mapped[str] = mapped_column(String(32))
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TerminalEvent(Base):
    __tablename__ = "terminal_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    terminal_id: Mapped[int] = mapped_column(ForeignKey("terminals.id", ondelete="CASCADE"))
    event_type: Mapped[TerminalEventType] = mapped_column(_enum(TerminalEventType, "terminal_event_type"))
    error_code: Mapped[str | None] = mapped_column(String(16))
    detail: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_terminal_events_terminal_time", "terminal_id", "occurred_at"),)


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    terminal_id: Mapped[int | None] = mapped_column(ForeignKey("terminals.id", ondelete="SET NULL"))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    method: Mapped[PaymentMethod] = mapped_column(_enum(PaymentMethod, "payment_method"))
    installments: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[TransactionStatus] = mapped_column(_enum(TransactionStatus, "transaction_status"))
    decline_code: Mapped[str | None] = mapped_column(String(8))
    decline_reason: Mapped[str | None] = mapped_column(String(200))
    card_brand_masked: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("installments BETWEEN 1 AND 48", name="installments_range"),
        CheckConstraint("status <> 'declined' OR decline_code IS NOT NULL", name="declined_has_code"),
        Index("ix_transactions_customer_time", "customer_id", "occurred_at"),
    )


class Settlement(Base):
    __tablename__ = "settlements"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    sales_date: Mapped[date] = mapped_column(Date)
    method: Mapped[PaymentMethod] = mapped_column(_enum(PaymentMethod, "settlement_method"))
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    fees: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    net_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    status: Mapped[SettlementStatus] = mapped_column(_enum(SettlementStatus, "settlement_status"))
    expected_date: Mapped[date] = mapped_column(Date)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    transaction_count: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        CheckConstraint("net_amount = gross_amount - fees", name="net_is_gross_minus_fees"),
        CheckConstraint("status <> 'completed' OR paid_at IS NOT NULL", name="completed_has_paid_at"),
        Index("ix_settlements_customer_expected", "customer_id", "expected_date"),
    )


class SupportCase(Base):
    __tablename__ = "support_cases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    conversation_id: Mapped[str | None] = mapped_column(String(64))
    category: Mapped[CaseCategory] = mapped_column(_enum(CaseCategory, "case_category"))
    priority: Mapped[CasePriority] = mapped_column(_enum(CasePriority, "case_priority"))
    status: Mapped[CaseStatus] = mapped_column(_enum(CaseStatus, "case_status"))
    subject: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    context_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    escalation_reasons: Mapped[list[str]] = mapped_column(JSONB, default=list)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    assigned_operator: Mapped[str | None] = mapped_column(String(64))
    interrupt_kind: Mapped[InterruptKind | None] = mapped_column(_enum(InterruptKind, "interrupt_kind"))
    interrupt_checkpoint_id: Mapped[str | None] = mapped_column(String(64))
    awaiting_human: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    sla_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_breaches: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now(), onupdate=_now())
    first_human_action_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_code: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        Index("ix_support_cases_customer_status", "customer_id", "status"),
        Index("ix_support_cases_queue", "status", "priority", "created_at"),
    )


class ServiceOrder(Base):
    """Created only by approval-gated actions (e.g. terminal replacement) after human approval."""

    __tablename__ = "service_orders"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    terminal_id: Mapped[int] = mapped_column(ForeignKey("terminals.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[ServiceOrderStatus] = mapped_column(_enum(ServiceOrderStatus, "service_order_status"))
    case_id: Mapped[str | None] = mapped_column(ForeignKey("support_cases.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


# ----------------------------------------------------------------------------------- platform
class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[ConversationStatus] = mapped_column(_enum(ConversationStatus, "conversation_status"))
    language: Mapped[str | None] = mapped_column(String(8))
    turn_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    slots_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    summary_upto_turn: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    open_case_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        Index("ix_conversations_customer_updated", "customer_id", "updated_at"),
        Index("ix_conversations_expires", "expires_at"),
    )


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    turn: Mapped[int] = mapped_column(Integer)
    role: Mapped[TurnRole] = mapped_column(_enum(TurnRole, "turn_role"))
    content: Mapped[str] = mapped_column(Text)  # always redacted before insert
    intent: Mapped[str | None] = mapped_column(String(48))
    agents: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")
    sources_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    status: Mapped[str | None] = mapped_column(String(32))
    case_id: Mapped[str | None] = mapped_column(String(32))
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    __table_args__ = (Index("ix_conversation_turns_conv_id", "conversation_id", "id"),)


class PendingAction(Base):
    __tablename__ = "pending_actions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("support_cases.id", ondelete="CASCADE"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    tool: Mapped[str] = mapped_column(String(64))
    arguments_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    policy: Mapped[ActionPolicy] = mapped_column(_enum(ActionPolicy, "action_policy"))
    status: Mapped[ActionStatus] = mapped_column(_enum(ActionStatus, "action_status"))
    decided_by: Mapped[str | None] = mapped_column(String(64))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())


class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    topic: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[OutboxStatus] = mapped_column(_enum(OutboxStatus, "outbox_status"))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_outbox_status_next", "status", "next_attempt_at"),)


class AuditLog(Base):
    """Append-only: a database trigger rejects UPDATE and DELETE (see migration 0001)."""

    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_type: Mapped[ActorType] = mapped_column(_enum(ActorType, "actor_type"))
    actor_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    entity: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    before_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_now())
    __table_args__ = (Index("ix_audit_entity", "entity", "entity_id"),)
