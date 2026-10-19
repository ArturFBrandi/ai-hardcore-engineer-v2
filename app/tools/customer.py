"""Read-only Customer Support tools (auto_allowed).

Each tool: typed Pydantic input → customer-scoped repository → typed JSON-safe output. Date presets are
resolved by code in the Brazilian business calendar, so the LLM never does date arithmetic.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from app.db.models import Settlement, SupportCase, Terminal, TerminalEvent, TerminalEventType, Transaction
from app.db.repositories import CustomerDataRepository
from app.errors import NotFoundError, ToolError
from app.tools.base import Tool, ToolInput
from app.tools.context import AuthContext

BR_TZ = ZoneInfo("America/Sao_Paulo")
MAX_RANGE_DAYS = 90

DatePreset = Literal["today", "yesterday", "day_before_yesterday", "last_7_days", "last_30_days"]


def br_today(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(BR_TZ).date()


def resolve_preset(preset: DatePreset, now: datetime | None = None) -> tuple[date, date]:
    today = br_today(now)
    match preset:
        case "today":
            return today, today
        case "yesterday":
            return today - timedelta(days=1), today - timedelta(days=1)
        case "day_before_yesterday":
            return today - timedelta(days=2), today - timedelta(days=2)
        case "last_7_days":
            return today - timedelta(days=6), today
        case "last_30_days":
            return today - timedelta(days=29), today


def day_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    lo = datetime.combine(start, time.min, tzinfo=BR_TZ).astimezone(UTC)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=BR_TZ).astimezone(UTC)
    return lo, hi


def _money(value: Decimal) -> str:
    return f"{value:.2f}"


class _DateRange(ToolInput):
    date_preset: DatePreset | None = Field(default=None, description="Relative period resolved by the system.")
    start_date: date | None = Field(default=None, description="Inclusive start (YYYY-MM-DD) if no preset.")
    end_date: date | None = Field(default=None, description="Inclusive end (YYYY-MM-DD) if no preset.")

    @model_validator(mode="after")
    def _check_range(self) -> _DateRange:
        if self.date_preset and (self.start_date or self.end_date):
            raise ValueError("use either date_preset or start_date/end_date")
        if self.start_date and self.end_date:
            if self.start_date > self.end_date:
                raise ValueError("start_date must be <= end_date")
            if (self.end_date - self.start_date).days > MAX_RANGE_DAYS:
                raise ValueError(f"range larger than {MAX_RANGE_DAYS} days")
        if self.start_date and self.start_date > br_today() + timedelta(days=60):
            raise ValueError("start_date too far in the future")
        return self

    def resolve(self, default: DatePreset) -> tuple[date, date]:
        if self.date_preset:
            return resolve_preset(self.date_preset)
        if self.start_date or self.end_date:
            start = self.start_date or (self.end_date or br_today()) - timedelta(days=6)
            end = self.end_date or start + timedelta(days=6)
            return start, end
        return resolve_preset(default)


# ------------------------------------------------------------------------------- serializers
def terminal_dict(t: Terminal) -> dict[str, Any]:
    return {
        "terminal_id": t.id,
        "model": t.model,
        "serial_number": t.serial_number,
        "status": t.status.value,
        "connectivity": t.connectivity.value,
        "firmware_version": t.firmware_version,
        "last_seen_at": t.last_seen_at.isoformat() if t.last_seen_at else None,
    }


def event_dict(e: TerminalEvent) -> dict[str, Any]:
    return {
        "event_type": e.event_type.value,
        "error_code": e.error_code,
        "detail": e.detail,
        "occurred_at": e.occurred_at.isoformat(),
    }


def txn_dict(t: Transaction) -> dict[str, Any]:
    return {
        "transaction_id": t.id,
        "amount": _money(t.amount),
        "currency": t.currency,
        "method": t.method.value,
        "installments": t.installments,
        "status": t.status.value,
        "decline_code": t.decline_code,
        "decline_reason": t.decline_reason,
        "card": t.card_brand_masked,
        "terminal_id": t.terminal_id,
        "occurred_at": t.occurred_at.astimezone(BR_TZ).isoformat(),
    }


def settlement_dict(s: Settlement) -> dict[str, Any]:
    return {
        "settlement_id": s.id,
        "sales_date": s.sales_date.isoformat(),
        "method": s.method.value,
        "gross_amount": _money(s.gross_amount),
        "fees": _money(s.fees),
        "net_amount": _money(s.net_amount),
        "status": s.status.value,
        "expected_date": s.expected_date.isoformat(),
        "paid_at": s.paid_at.astimezone(BR_TZ).isoformat() if s.paid_at else None,
        "transaction_count": s.transaction_count,
    }


def case_dict(c: SupportCase) -> dict[str, Any]:
    return {
        "case_id": c.id,
        "category": c.category.value,
        "priority": c.priority.value,
        "status": c.status.value,
        "subject": c.subject,
        "created_at": c.created_at.isoformat(),
        "updated_at": c.updated_at.isoformat(),
        "sla_due_at": c.sla_due_at.isoformat() if c.sla_due_at else None,
    }


# ------------------------------------------------------------------------------------- tools
class NoArgs(ToolInput):
    pass


class GetCustomerProfile(Tool[NoArgs]):
    name = "get_customer_profile"
    description = "Get the authenticated merchant's profile: plan, settlement terms (D+N), Pix and anticipation flags."
    input_model = NoArgs

    async def run(self, ctx: AuthContext, args: NoArgs) -> dict[str, Any]:
        async with self.db.session() as s:
            c = await CustomerDataRepository(s, ctx).profile()
        if c is None:
            raise NotFoundError("customer profile not found")
        return {
            "customer_id": c.id,
            "trade_name": c.trade_name,
            "segment": c.segment.value,
            "plan": c.plan,
            "pix_enabled": c.pix_enabled,
            "anticipation_enabled": c.anticipation_enabled,
            "credit_settlement_days": c.settlement_days,
            "bank_account": c.bank_masked,
            "synthetic": c.is_synthetic,
        }


class GetCustomerTerminals(Tool[NoArgs]):
    name = "get_customer_terminals"
    description = "List the merchant's card machines (terminals) with model, status and connectivity."
    input_model = NoArgs

    async def run(self, ctx: AuthContext, args: NoArgs) -> dict[str, Any]:
        async with self.db.session() as s:
            terminals = await CustomerDataRepository(s, ctx).terminals()
        return {"terminals": [terminal_dict(t) for t in terminals], "count": len(terminals)}


class TerminalStatusIn(ToolInput):
    terminal_id: int = Field(gt=0, description="Terminal id from get_customer_terminals.")
    lookback_days: int = Field(default=7, ge=1, le=30)


class GetTerminalStatus(Tool[TerminalStatusIn]):
    name = "get_terminal_status"
    description = "Get one terminal's status plus recent events (connectivity losses, error codes, reboots)."
    input_model = TerminalStatusIn

    async def run(self, ctx: AuthContext, args: TerminalStatusIn) -> dict[str, Any]:
        now = datetime.now(UTC)
        async with self.db.session() as s:
            repo = CustomerDataRepository(s, ctx)
            terminal = await repo.terminal(args.terminal_id)
            if terminal is None:
                raise NotFoundError("terminal not found")
            events = await repo.terminal_events(terminal.id, now - timedelta(days=args.lookback_days))
        losses_24h = sum(
            1
            for e in events
            if e.event_type is TerminalEventType.CONNECTIVITY_LOSS and e.occurred_at >= now - timedelta(hours=24)
        )
        error_codes = [e.error_code for e in events if e.event_type is TerminalEventType.ERROR_CODE and e.error_code]
        return {
            **terminal_dict(terminal),
            "recent_events": [event_dict(e) for e in events],
            "diagnostics": {
                "connectivity_losses_24h": losses_24h,
                "recent_error_codes": error_codes,
                "has_connectivity_issue": losses_24h > 0,
            },
        }


class TransactionsIn(_DateRange):
    status: Literal["approved", "declined", "pending", "refunded"] | None = None
    method: Literal["credit", "debit", "pix", "payment_link", "crediario"] | None = None
    limit: int = Field(default=20, ge=1, le=50)


class GetRecentTransactions(Tool[TransactionsIn]):
    name = "get_recent_transactions"
    description = (
        "List the merchant's sales in a period (default last 7 days). Prefer date_preset "
        "(today, yesterday, day_before_yesterday, last_7_days, last_30_days). Optional status/method filters."
    )
    input_model = TransactionsIn

    async def run(self, ctx: AuthContext, args: TransactionsIn) -> dict[str, Any]:
        start, end = args.resolve("last_7_days")
        lo, hi = day_bounds(start, end)
        async with self.db.session() as s:
            repo = CustomerDataRepository(s, ctx)
            rows = await repo.transactions(lo, hi, args.status, args.method, args.limit)
            totals = await repo.transaction_totals(lo, hi)
        return {
            "period": {"start": start.isoformat(), "end": end.isoformat(), "preset": args.date_preset},
            "transactions": [txn_dict(t) for t in rows],
            "totals_by_status": totals,
            "returned": len(rows),
        }


class TransactionDetailsIn(ToolInput):
    transaction_id: str = Field(pattern=r"^TXN-[A-F0-9]{10}$")


class GetTransactionDetails(Tool[TransactionDetailsIn]):
    name = "get_transaction_details"
    description = "Get one transaction by id, including decline code and reason when declined."
    input_model = TransactionDetailsIn

    async def run(self, ctx: AuthContext, args: TransactionDetailsIn) -> dict[str, Any]:
        async with self.db.session() as s:
            t = await CustomerDataRepository(s, ctx).transaction(args.transaction_id)
        if t is None:
            raise NotFoundError("transaction not found")
        return txn_dict(t)


class SettlementIn(_DateRange):
    by: Literal["sales_date", "expected_date"] = Field(
        default="sales_date",
        description="Filter by the date of the sales (default) or by the expected deposit date.",
    )


class GetSettlementSchedule(Tool[SettlementIn]):
    name = "get_settlement_schedule"
    description = (
        "Get settlements (deposits) with gross, fees, net, status and expected deposit date. "
        "Use by=sales_date with date_preset=yesterday for 'when will yesterday's sales be deposited'."
    )
    input_model = SettlementIn

    async def run(self, ctx: AuthContext, args: SettlementIn) -> dict[str, Any]:
        default: DatePreset = "yesterday" if args.by == "sales_date" else "last_7_days"
        start, end = args.resolve(default)
        if args.by == "expected_date" and not (args.date_preset or args.start_date or args.end_date):
            start, end = br_today() - timedelta(days=7), br_today() + timedelta(days=45)
        async with self.db.session() as s:
            repo = CustomerDataRepository(s, ctx)
            profile = await repo.profile()
            if profile is None:
                raise NotFoundError("customer not found")
            rows = (
                await repo.settlements_by_sales_date(start, end)
                if args.by == "sales_date"
                else await repo.settlements_by_expected_date(start, end)
            )
        net_total = sum((r.net_amount for r in rows), Decimal("0"))
        return {
            "period": {"by": args.by, "start": start.isoformat(), "end": end.isoformat()},
            "today": br_today().isoformat(),
            "settlement_terms": {"credit_days": profile.settlement_days, "debit_days": 1, "pix": "same day"},
            "settlements": [settlement_dict(r) for r in rows],
            "net_total": _money(net_total),
        }


class GetOpenSupportCases(Tool[NoArgs]):
    name = "get_open_support_cases"
    description = "List the merchant's open/in-progress support cases (tickets) and their status."
    input_model = NoArgs

    async def run(self, ctx: AuthContext, args: NoArgs) -> dict[str, Any]:
        async with self.db.session() as s:
            cases = await CustomerDataRepository(s, ctx).open_cases()
        return {"cases": [case_dict(c) for c in cases], "count": len(cases)}


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise ToolError(message)
