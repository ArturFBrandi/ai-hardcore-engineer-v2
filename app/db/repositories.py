"""Customer-scoped repositories over the synthetic support backend.

Every query is parameterized and automatically filtered by ``AuthContext.customer_id``. A row owned by
another customer is indistinguishable from a missing row (``None``) — no existence oracle.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ActorType,
    AuditLog,
    CaseStatus,
    Customer,
    Settlement,
    SupportCase,
    Terminal,
    TerminalEvent,
    Transaction,
)
from app.tools.context import AuthContext

OPEN_CASE_STATUSES = (CaseStatus.OPEN, CaseStatus.IN_PROGRESS, CaseStatus.AWAITING_CUSTOMER)


class CustomerDataRepository:
    def __init__(self, session: AsyncSession, ctx: AuthContext) -> None:
        self._s = session
        self._cid = ctx.customer_id

    async def profile(self) -> Customer | None:
        return await self._s.scalar(select(Customer).where(Customer.id == self._cid))

    async def terminals(self) -> list[Terminal]:
        rows = await self._s.scalars(select(Terminal).where(Terminal.customer_id == self._cid).order_by(Terminal.id))
        return list(rows)

    async def terminal(self, terminal_id: int) -> Terminal | None:
        return await self._s.scalar(
            select(Terminal).where(Terminal.id == terminal_id, Terminal.customer_id == self._cid)
        )

    async def terminal_events(self, terminal_id: int, since: datetime) -> list[TerminalEvent]:
        stmt = (
            select(TerminalEvent)
            .join(Terminal, Terminal.id == TerminalEvent.terminal_id)
            .where(Terminal.customer_id == self._cid, TerminalEvent.terminal_id == terminal_id)
            .where(TerminalEvent.occurred_at >= since)
            .order_by(TerminalEvent.occurred_at.desc())
            .limit(50)
        )
        return list(await self._s.scalars(stmt))

    async def transactions(
        self,
        start: datetime,
        end: datetime,
        status: str | None = None,
        method: str | None = None,
        limit: int = 50,
    ) -> list[Transaction]:
        stmt = select(Transaction).where(
            Transaction.customer_id == self._cid,
            Transaction.occurred_at >= start,
            Transaction.occurred_at < end,
        )
        if status:
            stmt = stmt.where(Transaction.status == status)
        if method:
            stmt = stmt.where(Transaction.method == method)
        stmt = stmt.order_by(Transaction.occurred_at.desc()).limit(limit)
        return list(await self._s.scalars(stmt))

    async def transaction_totals(self, start: datetime, end: datetime) -> dict[str, Any]:
        stmt = (
            select(Transaction.status, func.count(), func.coalesce(func.sum(Transaction.amount), 0))
            .where(
                Transaction.customer_id == self._cid,
                Transaction.occurred_at >= start,
                Transaction.occurred_at < end,
            )
            .group_by(Transaction.status)
        )
        return {str(row[0].value): {"count": int(row[1]), "amount": str(row[2])} for row in await self._s.execute(stmt)}

    async def transaction(self, transaction_id: str) -> Transaction | None:
        return await self._s.scalar(
            select(Transaction).where(Transaction.id == transaction_id, Transaction.customer_id == self._cid)
        )

    async def settlements_by_sales_date(self, start: date, end: date) -> list[Settlement]:
        stmt = (
            select(Settlement)
            .where(Settlement.customer_id == self._cid, Settlement.sales_date >= start, Settlement.sales_date <= end)
            .order_by(Settlement.sales_date, Settlement.method)
        )
        return list(await self._s.scalars(stmt))

    async def settlements_by_expected_date(self, start: date, end: date) -> list[Settlement]:
        stmt = (
            select(Settlement)
            .where(
                Settlement.customer_id == self._cid,
                Settlement.expected_date >= start,
                Settlement.expected_date <= end,
            )
            .order_by(Settlement.expected_date, Settlement.method)
        )
        return list(await self._s.scalars(stmt))

    async def open_cases(self) -> list[SupportCase]:
        stmt = (
            select(SupportCase)
            .where(SupportCase.customer_id == self._cid, SupportCase.status.in_(OPEN_CASE_STATUSES))
            .order_by(SupportCase.created_at.desc())
            .limit(20)
        )
        return list(await self._s.scalars(stmt))

    async def case(self, case_id: str) -> SupportCase | None:
        return await self._s.scalar(
            select(SupportCase).where(SupportCase.id == case_id, SupportCase.customer_id == self._cid)
        )


async def next_case_id(session: AsyncSession, now: datetime | None = None) -> str:
    seq = await session.scalar(text("SELECT nextval('support_case_seq')"))
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d")
    return f"CASE-{stamp}-{int(seq or 0):04d}"


async def next_business_id(session: AsyncSession, prefix: str) -> str:
    seq = await session.scalar(text("SELECT nextval('business_id_seq')"))
    return f"{prefix}-{int(seq or 0):06d}"


async def write_audit(
    session: AsyncSession,
    *,
    actor_type: ActorType,
    actor_id: str,
    action: str,
    entity: str,
    entity_id: str,
    request_id: str | None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            entity=entity,
            entity_id=entity_id,
            request_id=request_id,
            before_json=before,
            after_json=after,
        )
    )


def window_start(days: int, now: datetime | None = None) -> datetime:
    return (now or datetime.now(UTC)) - timedelta(days=days)
