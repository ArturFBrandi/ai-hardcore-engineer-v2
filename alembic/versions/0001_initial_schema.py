"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-19 15:11:42.313572
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from sqlalchemy.dialects import postgresql
revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('actor_type', sa.Enum('customer', 'agent', 'operator', 'system', name='actor_type', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('actor_id', sa.String(length=64), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('entity', sa.String(length=64), nullable=False),
    sa.Column('entity_id', sa.String(length=64), nullable=False),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('before_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('after_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("actor_type IN ('customer', 'agent', 'operator', 'system')", name=op.f('ck_audit_log_actor_type')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_log'))
    )
    op.create_index('ix_audit_entity', 'audit_log', ['entity', 'entity_id'], unique=False)
    op.create_table('conversations',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('customer_id', sa.String(length=64), nullable=False),
    sa.Column('status', sa.Enum('active', 'awaiting_human', 'closed', name='conversation_status', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('language', sa.String(length=8), nullable=True),
    sa.Column('turn_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('slots_json', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('summary_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('summary_upto_turn', sa.Integer(), server_default='0', nullable=False),
    sa.Column('open_case_id', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('active', 'awaiting_human', 'closed')", name=op.f('ck_conversations_conversation_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversations'))
    )
    op.create_index('ix_conversations_customer_updated', 'conversations', ['customer_id', 'updated_at'], unique=False)
    op.create_index('ix_conversations_expires', 'conversations', ['expires_at'], unique=False)
    op.create_table('customers',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('trade_name', sa.String(length=200), nullable=False),
    sa.Column('document_masked', sa.String(length=32), nullable=False),
    sa.Column('email_masked', sa.String(length=200), nullable=False),
    sa.Column('segment', sa.Enum('mei', 'small_business', 'middle_market', name='segment', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('plan', sa.String(length=64), nullable=False),
    sa.Column('pix_enabled', sa.Boolean(), nullable=False),
    sa.Column('anticipation_enabled', sa.Boolean(), nullable=False),
    sa.Column('settlement_days', sa.Integer(), nullable=False),
    sa.Column('bank_masked', sa.String(length=64), nullable=False),
    sa.Column('is_synthetic', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("segment IN ('mei', 'small_business', 'middle_market')", name=op.f('ck_customers_segment')),
    sa.CheckConstraint('settlement_days BETWEEN 0 AND 60', name=op.f('ck_customers_settlement_days_range')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_customers'))
    )
    op.create_table('outbox',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('topic', sa.String(length=64), nullable=False),
    sa.Column('payload_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', sa.Enum('pending', 'delivered', 'dead', name='outbox_status', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('pending', 'delivered', 'dead')", name=op.f('ck_outbox_outbox_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_outbox'))
    )
    op.create_index('ix_outbox_status_next', 'outbox', ['status', 'next_attempt_at'], unique=False)
    op.create_table('conversation_turns',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('conversation_id', sa.String(length=64), nullable=False),
    sa.Column('turn', sa.Integer(), nullable=False),
    sa.Column('role', sa.Enum('customer', 'assistant', 'human_operator', 'system_note', name='turn_role', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('intent', sa.String(length=48), nullable=True),
    sa.Column('agents', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('sources_json', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('status', sa.String(length=32), nullable=True),
    sa.Column('case_id', sa.String(length=32), nullable=True),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("role IN ('customer', 'assistant', 'human_operator', 'system_note')", name=op.f('ck_conversation_turns_turn_role')),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name=op.f('fk_conversation_turns_conversation_id_conversations'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_conversation_turns'))
    )
    op.create_index('ix_conversation_turns_conv_id', 'conversation_turns', ['conversation_id', 'id'], unique=False)
    op.create_table('settlements',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('customer_id', sa.String(length=64), nullable=False),
    sa.Column('sales_date', sa.Date(), nullable=False),
    sa.Column('method', sa.Enum('credit', 'debit', 'pix', 'payment_link', 'crediario', name='settlement_method', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('gross_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('fees', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('net_amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('status', sa.Enum('completed', 'pending', 'scheduled', name='settlement_status', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('expected_date', sa.Date(), nullable=False),
    sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('transaction_count', sa.Integer(), nullable=False),
    sa.CheckConstraint("method IN ('credit', 'debit', 'pix', 'payment_link', 'crediario')", name=op.f('ck_settlements_settlement_method')),
    sa.CheckConstraint("status <> 'completed' OR paid_at IS NOT NULL", name=op.f('ck_settlements_completed_has_paid_at')),
    sa.CheckConstraint("status IN ('completed', 'pending', 'scheduled')", name=op.f('ck_settlements_settlement_status')),
    sa.CheckConstraint('net_amount = gross_amount - fees', name=op.f('ck_settlements_net_is_gross_minus_fees')),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_settlements_customer_id_customers'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_settlements'))
    )
    op.create_index('ix_settlements_customer_expected', 'settlements', ['customer_id', 'expected_date'], unique=False)
    op.create_table('support_cases',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('customer_id', sa.String(length=64), nullable=False),
    sa.Column('conversation_id', sa.String(length=64), nullable=True),
    sa.Column('category', sa.Enum('POS_CONNECTIVITY', 'POS_HARDWARE', 'TRANSACTION_ISSUE', 'SETTLEMENT_ISSUE', 'ACCOUNT_ISSUE', 'PRODUCT_QUESTION', 'GENERAL_REQUEST', 'SECURITY_CONCERN', name='case_category', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('priority', sa.Enum('low', 'normal', 'high', 'urgent', name='case_priority', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('status', sa.Enum('open', 'in_progress', 'awaiting_customer', 'resolved', 'closed', name='case_status', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('context_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('escalation_reasons', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('idempotency_key', sa.String(length=128), nullable=True),
    sa.Column('assigned_operator', sa.String(length=64), nullable=True),
    sa.Column('interrupt_kind', sa.Enum('reply', 'approval', name='interrupt_kind', native_enum=False, create_constraint=False, length=32), nullable=True),
    sa.Column('interrupt_checkpoint_id', sa.String(length=64), nullable=True),
    sa.Column('awaiting_human', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('sla_due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sla_breaches', sa.Integer(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('first_human_action_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolution_code', sa.String(length=64), nullable=True),
    sa.CheckConstraint("category IN ('POS_CONNECTIVITY', 'POS_HARDWARE', 'TRANSACTION_ISSUE', 'SETTLEMENT_ISSUE', 'ACCOUNT_ISSUE', 'PRODUCT_QUESTION', 'GENERAL_REQUEST', 'SECURITY_CONCERN')", name=op.f('ck_support_cases_case_category')),
    sa.CheckConstraint("interrupt_kind IN ('reply', 'approval')", name=op.f('ck_support_cases_interrupt_kind')),
    sa.CheckConstraint("priority IN ('low', 'normal', 'high', 'urgent')", name=op.f('ck_support_cases_case_priority')),
    sa.CheckConstraint("status IN ('open', 'in_progress', 'awaiting_customer', 'resolved', 'closed')", name=op.f('ck_support_cases_case_status')),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_support_cases_customer_id_customers'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_support_cases')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_support_cases_idempotency_key'))
    )
    op.create_index('ix_support_cases_customer_status', 'support_cases', ['customer_id', 'status'], unique=False)
    op.create_index('ix_support_cases_queue', 'support_cases', ['status', 'priority', 'created_at'], unique=False)
    op.create_table('terminals',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('customer_id', sa.String(length=64), nullable=False),
    sa.Column('serial_number', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=64), nullable=False),
    sa.Column('status', sa.Enum('active', 'inactive', 'maintenance', 'blocked', name='terminal_status', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('connectivity', sa.Enum('wifi', '4g', 'wifi+4g', name='connectivity', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('firmware_version', sa.String(length=32), nullable=False),
    sa.Column('activated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("connectivity IN ('wifi', '4g', 'wifi+4g')", name=op.f('ck_terminals_connectivity')),
    sa.CheckConstraint("status IN ('active', 'inactive', 'maintenance', 'blocked')", name=op.f('ck_terminals_terminal_status')),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_terminals_customer_id_customers'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_terminals')),
    sa.UniqueConstraint('serial_number', name=op.f('uq_terminals_serial_number'))
    )
    op.create_index(op.f('ix_terminals_customer_id'), 'terminals', ['customer_id'], unique=False)
    op.create_table('pending_actions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('case_id', sa.String(length=32), nullable=True),
    sa.Column('customer_id', sa.String(length=64), nullable=False),
    sa.Column('tool', sa.String(length=64), nullable=False),
    sa.Column('arguments_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('policy', sa.Enum('auto_allowed', 'requires_approval', 'forbidden', name='action_policy', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('status', sa.Enum('proposed', 'approved', 'rejected', 'executed', 'failed', name='action_status', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('decided_by', sa.String(length=64), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('result_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("policy IN ('auto_allowed', 'requires_approval', 'forbidden')", name=op.f('ck_pending_actions_action_policy')),
    sa.CheckConstraint("status IN ('proposed', 'approved', 'rejected', 'executed', 'failed')", name=op.f('ck_pending_actions_action_status')),
    sa.ForeignKeyConstraint(['case_id'], ['support_cases.id'], name=op.f('fk_pending_actions_case_id_support_cases'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_pending_actions_customer_id_customers'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pending_actions'))
    )
    op.create_index(op.f('ix_pending_actions_case_id'), 'pending_actions', ['case_id'], unique=False)
    op.create_table('service_orders',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('customer_id', sa.String(length=64), nullable=False),
    sa.Column('terminal_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('requested', 'scheduled', 'completed', 'cancelled', name='service_order_status', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('case_id', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('requested', 'scheduled', 'completed', 'cancelled')", name=op.f('ck_service_orders_service_order_status')),
    sa.ForeignKeyConstraint(['case_id'], ['support_cases.id'], name=op.f('fk_service_orders_case_id_support_cases'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_service_orders_customer_id_customers'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['terminal_id'], ['terminals.id'], name=op.f('fk_service_orders_terminal_id_terminals'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_service_orders'))
    )
    op.create_index(op.f('ix_service_orders_customer_id'), 'service_orders', ['customer_id'], unique=False)
    op.create_table('terminal_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('terminal_id', sa.Integer(), nullable=False),
    sa.Column('event_type', sa.Enum('connectivity_loss', 'connectivity_restored', 'error_code', 'reboot', 'firmware_update', 'low_battery', name='terminal_event_type', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('error_code', sa.String(length=16), nullable=True),
    sa.Column('detail', sa.Text(), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("event_type IN ('connectivity_loss', 'connectivity_restored', 'error_code', 'reboot', 'firmware_update', 'low_battery')", name=op.f('ck_terminal_events_terminal_event_type')),
    sa.ForeignKeyConstraint(['terminal_id'], ['terminals.id'], name=op.f('fk_terminal_events_terminal_id_terminals'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_terminal_events'))
    )
    op.create_index('ix_terminal_events_terminal_time', 'terminal_events', ['terminal_id', 'occurred_at'], unique=False)
    op.create_table('transactions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('customer_id', sa.String(length=64), nullable=False),
    sa.Column('terminal_id', sa.Integer(), nullable=True),
    sa.Column('amount', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('method', sa.Enum('credit', 'debit', 'pix', 'payment_link', 'crediario', name='payment_method', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('installments', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('approved', 'declined', 'pending', 'refunded', name='transaction_status', native_enum=False, create_constraint=False, length=32), nullable=False),
    sa.Column('decline_code', sa.String(length=8), nullable=True),
    sa.Column('decline_reason', sa.String(length=200), nullable=True),
    sa.Column('card_brand_masked', sa.String(length=64), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("method IN ('credit', 'debit', 'pix', 'payment_link', 'crediario')", name=op.f('ck_transactions_payment_method')),
    sa.CheckConstraint("status <> 'declined' OR decline_code IS NOT NULL", name=op.f('ck_transactions_declined_has_code')),
    sa.CheckConstraint("status IN ('approved', 'declined', 'pending', 'refunded')", name=op.f('ck_transactions_transaction_status')),
    sa.CheckConstraint('amount > 0', name=op.f('ck_transactions_amount_positive')),
    sa.CheckConstraint('installments BETWEEN 1 AND 48', name=op.f('ck_transactions_installments_range')),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], name=op.f('fk_transactions_customer_id_customers'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['terminal_id'], ['terminals.id'], name=op.f('fk_transactions_terminal_id_terminals'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transactions'))
    )
    op.create_index('ix_transactions_customer_time', 'transactions', ['customer_id', 'occurred_at'], unique=False)

    # Case ids are CASE-YYYYMMDD-NNNN; NNNN comes from this sequence (unique across replicas).
    op.execute("CREATE SEQUENCE IF NOT EXISTS support_case_seq START 1")
    op.execute("CREATE SEQUENCE IF NOT EXISTS business_id_seq START 1000")
    # audit_log is append-only: reject UPDATE and DELETE at the database level.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only (% rejected)', TG_OP;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_append_only()")
    op.execute("DROP SEQUENCE IF EXISTS support_case_seq")
    op.execute("DROP SEQUENCE IF EXISTS business_id_seq")
    op.drop_index('ix_transactions_customer_time', table_name='transactions')
    op.drop_table('transactions')
    op.drop_index('ix_terminal_events_terminal_time', table_name='terminal_events')
    op.drop_table('terminal_events')
    op.drop_index(op.f('ix_service_orders_customer_id'), table_name='service_orders')
    op.drop_table('service_orders')
    op.drop_index(op.f('ix_pending_actions_case_id'), table_name='pending_actions')
    op.drop_table('pending_actions')
    op.drop_index(op.f('ix_terminals_customer_id'), table_name='terminals')
    op.drop_table('terminals')
    op.drop_index('ix_support_cases_queue', table_name='support_cases')
    op.drop_index('ix_support_cases_customer_status', table_name='support_cases')
    op.drop_table('support_cases')
    op.drop_index('ix_settlements_customer_expected', table_name='settlements')
    op.drop_table('settlements')
    op.drop_index('ix_conversation_turns_conv_id', table_name='conversation_turns')
    op.drop_table('conversation_turns')
    op.drop_index('ix_outbox_status_next', table_name='outbox')
    op.drop_table('outbox')
    op.drop_table('customers')
    op.drop_index('ix_conversations_expires', table_name='conversations')
    op.drop_index('ix_conversations_customer_updated', table_name='conversations')
    op.drop_table('conversations')
    op.drop_index('ix_audit_entity', table_name='audit_log')
    op.drop_table('audit_log')
