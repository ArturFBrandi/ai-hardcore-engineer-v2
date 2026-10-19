"""Idempotent synthetic seed for the support backend.

ALL DATA IS SYNTHETIC. Names, documents, cards and bank data are fictitious and masked.
Dates are generated **relative to the seeding moment** (America/Sao_Paulo business calendar) so
"yesterday's sales" always exists. Re-running the seed replaces the seeded customers' backend rows
(terminals, events, transactions, settlements, seed cases) deterministically; runtime data created by
conversations (cases, conversations, audit) is left untouched.

Run: ``python -m app.db.seed`` (``make seed``).
"""

from __future__ import annotations

import asyncio
import hashlib
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.base import Database
from app.db.models import (
    CaseCategory,
    CasePriority,
    CaseStatus,
    Connectivity,
    Customer,
    PaymentMethod,
    Segment,
    Settlement,
    SettlementStatus,
    SupportCase,
    Terminal,
    TerminalEvent,
    TerminalEventType,
    TerminalStatus,
    Transaction,
    TransactionStatus,
)

BR_TZ = ZoneInfo("America/Sao_Paulo")
SEED_CUSTOMERS = ("cliente1988", "cliente2077")

# Fee schedule used ONLY to generate synthetic settlements (not presented as Getnet's real rates).
_SYNTHETIC_MDR = {
    PaymentMethod.DEBIT: Decimal("0.0137"),
    PaymentMethod.CREDIT: Decimal("0.0315"),
    PaymentMethod.PIX: Decimal("0.0000"),
    PaymentMethod.PAYMENT_LINK: Decimal("0.0399"),
    PaymentMethod.CREDIARIO: Decimal("0.0000"),
}

DECLINE_CODES = {
    "51": "Saldo insuficiente (insufficient funds)",
    "05": "Transação não autorizada pelo emissor (do not honor)",
    "54": "Cartão vencido (expired card)",
    "55": "Senha inválida (invalid PIN)",
    "57": "Transação não permitida para o cartão (transaction not permitted)",
}


@dataclass(frozen=True)
class SeedClock:
    now: datetime  # timezone-aware UTC

    @property
    def today(self) -> date:
        return self.now.astimezone(BR_TZ).date()

    def at(self, day: date, hour: int, minute: int = 0) -> datetime:
        return datetime.combine(day, time(hour, minute), tzinfo=BR_TZ).astimezone(UTC)


def _money(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _txn_id(customer_id: str, n: int) -> str:
    digest = hashlib.sha1(f"{customer_id}:{n}".encode(), usedforsecurity=False).hexdigest()[:10].upper()
    return f"TXN-{digest}"


def _settlement_date(sales_day: date, method: PaymentMethod, settlement_days: int) -> date:
    if method is PaymentMethod.PIX:
        return sales_day
    days = 1 if method is PaymentMethod.DEBIT else settlement_days
    if method is PaymentMethod.CREDIARIO:
        days = 30
    out = sales_day + timedelta(days=days)
    while out.weekday() >= 5:  # settlements land on business days
        out += timedelta(days=1)
    return out


CUSTOMERS = [
    {
        "id": "cliente1988",
        "name": "Padaria Pão Dourado Ltda (SINTÉTICO)",
        "trade_name": "Padaria Pão Dourado",
        "document_masked": "**.***.456/0001-**",
        "email_masked": "c****o@paodourado.example",
        "segment": Segment.SMALL_BUSINESS,
        "plan": "Plano Controle (synthetic)",
        "pix_enabled": True,
        "anticipation_enabled": False,
        "settlement_days": 2,
        "bank_masked": "Banco 033 • Ag ****-1 • CC *****-7",
    },
    {
        "id": "cliente2077",
        "name": "Oficina Neon Motos ME (SINTÉTICO)",
        "trade_name": "Neon Motos",
        "document_masked": "**.***.789/0001-**",
        "email_masked": "n****s@neonmotos.example",
        "segment": Segment.MEI,
        "plan": "Plano Livre (synthetic)",
        "pix_enabled": False,
        "anticipation_enabled": True,
        "settlement_days": 30,
        "bank_masked": "Banco 341 • Ag ****-9 • CC *****-2",
    },
]


def _terminals(clock: SeedClock) -> list[dict[str, object]]:
    t = clock.now
    return [
        {
            "id": 1001,
            "customer_id": "cliente1988",
            "serial_number": "SYN-GS-0001988",
            "model": "Get Smart",
            "status": TerminalStatus.ACTIVE,
            "connectivity": Connectivity.WIFI_4G,
            "firmware_version": "3.14.2",
            "activated_at": t - timedelta(days=400),
            "last_seen_at": t - timedelta(hours=3),
        },
        {
            "id": 1002,
            "customer_id": "cliente1988",
            "serial_number": "SYN-GM-0001988",
            "model": "Get Mini",
            "status": TerminalStatus.ACTIVE,
            "connectivity": Connectivity.G4,
            "firmware_version": "2.8.0",
            "activated_at": t - timedelta(days=120),
            "last_seen_at": t - timedelta(minutes=20),
        },
        {
            "id": 2001,
            "customer_id": "cliente2077",
            "serial_number": "SYN-GC-0002077",
            "model": "Get Clássica",
            "status": TerminalStatus.ACTIVE,
            "connectivity": Connectivity.WIFI,
            "firmware_version": "1.9.7",
            "activated_at": t - timedelta(days=800),
            "last_seen_at": t - timedelta(hours=1),
        },
    ]


def _events(clock: SeedClock) -> list[dict[str, object]]:
    t = clock.now
    return [
        # cliente1988 Get Smart: repeated connectivity loss in the last 24h (Workflow 2).
        {
            "terminal_id": 1001,
            "event_type": TerminalEventType.CONNECTIVITY_LOSS,
            "error_code": None,
            "detail": "Wi-Fi signal lost; 4G fallback failed (no APN response)",
            "occurred_at": t - timedelta(hours=20),
        },
        {
            "terminal_id": 1001,
            "event_type": TerminalEventType.REBOOT,
            "error_code": None,
            "detail": "Manual reboot by merchant",
            "occurred_at": t - timedelta(hours=19, minutes=40),
        },
        {
            "terminal_id": 1001,
            "event_type": TerminalEventType.CONNECTIVITY_LOSS,
            "error_code": None,
            "detail": "Wi-Fi signal lost; 4G fallback failed (no APN response)",
            "occurred_at": t - timedelta(hours=4),
        },
        {
            "terminal_id": 1001,
            "event_type": TerminalEventType.ERROR_CODE,
            "error_code": "E-COM",
            "detail": "Communication error while contacting authorizer",
            "occurred_at": t - timedelta(hours=3, minutes=10),
        },
        {
            "terminal_id": 1002,
            "event_type": TerminalEventType.FIRMWARE_UPDATE,
            "error_code": None,
            "detail": "Firmware updated to 2.8.0",
            "occurred_at": t - timedelta(days=12),
        },
        # cliente2077 Get Clássica: low battery + a decline error code.
        {
            "terminal_id": 2001,
            "event_type": TerminalEventType.LOW_BATTERY,
            "error_code": None,
            "detail": "Battery below 15%",
            "occurred_at": t - timedelta(days=2),
        },
        {
            "terminal_id": 2001,
            "event_type": TerminalEventType.ERROR_CODE,
            "error_code": "51",
            "detail": "Issuer declined: insufficient funds",
            "occurred_at": t - timedelta(hours=5),
        },
    ]


def _transactions(clock: SeedClock) -> list[dict[str, object]]:
    rng = random.Random(1988)  # noqa: S311 - deterministic synthetic data, not security relevant
    rows: list[dict[str, object]] = []
    today = clock.today
    brands = ["Visa ****1234", "Mastercard ****5678", "Elo ****9012", "Amex ****3456"]

    def add(
        customer: str,
        n: int,
        day: date,
        hour: int,
        amount: float,
        method: PaymentMethod,
        status: TransactionStatus,
        terminal: int | None,
        installments: int = 1,
        code: str | None = None,
    ) -> None:
        rows.append(
            {
                "id": _txn_id(customer, n),
                "customer_id": customer,
                "terminal_id": terminal,
                "amount": _money(amount),
                "currency": "BRL",
                "method": method,
                "installments": installments,
                "status": status,
                "decline_code": code,
                "decline_reason": DECLINE_CODES.get(code or ""),
                "card_brand_masked": None if method in (PaymentMethod.PIX,) else brands[n % len(brands)],
                "occurred_at": clock.at(day, hour, (n * 7) % 60),
            }
        )

    n = 0
    # cliente1988 — fixed, explainable recent activity
    yesterday, before = today - timedelta(days=1), today - timedelta(days=2)
    fixed_1988 = [
        (yesterday, 8, 45.90, PaymentMethod.DEBIT, TransactionStatus.APPROVED, 1001, 1, None),
        (yesterday, 9, 120.00, PaymentMethod.CREDIT, TransactionStatus.APPROVED, 1001, 1, None),
        (yesterday, 11, 32.50, PaymentMethod.PIX, TransactionStatus.APPROVED, 1002, 1, None),
        (yesterday, 12, 89.70, PaymentMethod.CREDIT, TransactionStatus.APPROVED, 1002, 3, None),
        (yesterday, 15, 250.00, PaymentMethod.CREDIT, TransactionStatus.DECLINED, 1001, 1, "51"),
        (yesterday, 17, 18.00, PaymentMethod.DEBIT, TransactionStatus.APPROVED, 1002, 1, None),
        (before, 9, 64.00, PaymentMethod.DEBIT, TransactionStatus.APPROVED, 1001, 1, None),
        (before, 13, 310.00, PaymentMethod.CREDIT, TransactionStatus.APPROVED, 1001, 6, None),
        (before, 16, 27.80, PaymentMethod.PIX, TransactionStatus.APPROVED, 1002, 1, None),
        (today, 8, 15.50, PaymentMethod.DEBIT, TransactionStatus.APPROVED, 1002, 1, None),
        (today, 9, 199.90, PaymentMethod.CREDIT, TransactionStatus.DECLINED, 1001, 1, "05"),
        (today, 10, 75.00, PaymentMethod.CREDIT, TransactionStatus.PENDING, 1001, 1, None),
    ]
    for day, hour, amount, method, status, term, inst, code in fixed_1988:
        n += 1
        add("cliente1988", n, day, hour, amount, method, status, term, inst, code)
    # historical background for cliente1988 (3–30 days ago)
    for days_ago in range(3, 31):
        day = today - timedelta(days=days_ago)
        for _ in range(rng.randint(2, 5)):
            n += 1
            method = rng.choice([PaymentMethod.DEBIT, PaymentMethod.CREDIT, PaymentMethod.PIX])
            status = TransactionStatus.APPROVED if rng.random() > 0.08 else TransactionStatus.DECLINED
            code = rng.choice(list(DECLINE_CODES)) if status is TransactionStatus.DECLINED else None
            add(
                "cliente1988",
                n,
                day,
                rng.randint(8, 19),
                rng.uniform(10, 400),
                method,
                status,
                rng.choice([1001, 1002]),
                1,
                code,
            )
    # one refunded historical sale
    n += 1
    add("cliente1988", n, today - timedelta(days=6), 14, 59.90, PaymentMethod.CREDIT, TransactionStatus.REFUNDED, 1001)

    # cliente2077 — the isolation customer
    m = 5000
    fixed_2077 = [
        (yesterday, 10, 980.00, PaymentMethod.CREDIT, TransactionStatus.APPROVED, 2001, 10, None),
        (yesterday, 14, 150.00, PaymentMethod.DEBIT, TransactionStatus.APPROVED, 2001, 1, None),
        (today, 9, 420.00, PaymentMethod.CREDIT, TransactionStatus.DECLINED, 2001, 1, "51"),
        (before, 11, 2300.00, PaymentMethod.CREDIARIO, TransactionStatus.APPROVED, 2001, 12, None),
    ]
    for day, hour, amount, method, status, term, inst, code in fixed_2077:
        m += 1
        add("cliente2077", m, day, hour, amount, method, status, term, inst, code)
    return rows


def _settlements(
    clock: SeedClock, txns: list[dict[str, object]], settlement_days: dict[str, int]
) -> list[dict[str, object]]:
    groups: dict[tuple[str, date, PaymentMethod], list[Decimal]] = {}
    for t in txns:
        if t["status"] is not TransactionStatus.APPROVED:
            continue
        occurred = t["occurred_at"]
        assert isinstance(occurred, datetime)  # noqa: S101 - narrowing for mypy
        key = (str(t["customer_id"]), occurred.astimezone(BR_TZ).date(), t["method"])
        amount = t["amount"]
        assert isinstance(amount, Decimal)  # noqa: S101
        groups.setdefault(key, []).append(amount)  # type: ignore[arg-type]
    rows = []
    for (customer, sales_day, method), amounts in sorted(
        groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2].value)
    ):
        gross = _money(sum(amounts, Decimal("0")))
        fees = _money(gross * _SYNTHETIC_MDR[method])
        expected = _settlement_date(sales_day, method, settlement_days[customer])
        if expected < clock.today:
            status, paid_at = SettlementStatus.COMPLETED, clock.at(expected, 9)
        elif expected == clock.today:
            status, paid_at = SettlementStatus.PENDING, None
        else:
            status, paid_at = SettlementStatus.SCHEDULED, None
        sid = f"STL-{customer[-4:]}-{sales_day:%Y%m%d}-{method.value[:3].upper()}"
        rows.append(
            {
                "id": sid,
                "customer_id": customer,
                "sales_date": sales_day,
                "method": method,
                "gross_amount": gross,
                "fees": fees,
                "net_amount": gross - fees,
                "status": status,
                "expected_date": expected,
                "paid_at": paid_at,
                "transaction_count": len(amounts),
            }
        )
    return rows


def _cases(clock: SeedClock) -> list[dict[str, object]]:
    t = clock.now
    return [
        {
            "id": "CASE-SEED-0001",
            "customer_id": "cliente1988",
            "conversation_id": None,
            "category": CaseCategory.POS_HARDWARE,
            "priority": CasePriority.NORMAL,
            "status": CaseStatus.RESOLVED,
            "subject": "Troca de bobina / impressora falhando",
            "description": "Historical synthetic case.",
            "context_json": {"synthetic": True},
            "escalation_reasons": [],
            "idempotency_key": "seed:CASE-SEED-0001",
            "created_at": t - timedelta(days=45),
            "updated_at": t - timedelta(days=43),
            "resolved_at": t - timedelta(days=43),
            "resolution_code": "PAPER_ROLL_SENT",
        },
        {
            "id": "CASE-SEED-0002",
            "customer_id": "cliente1988",
            "conversation_id": None,
            "category": CaseCategory.SETTLEMENT_ISSUE,
            "priority": CasePriority.NORMAL,
            "status": CaseStatus.IN_PROGRESS,
            "subject": "Divergência em repasse de crédito parcelado",
            "description": "Open synthetic case under analysis.",
            "context_json": {"synthetic": True},
            "escalation_reasons": [],
            "idempotency_key": "seed:CASE-SEED-0002",
            "assigned_operator": "operator.ana",
            "created_at": t - timedelta(days=3),
            "updated_at": t - timedelta(days=1),
            "sla_due_at": t + timedelta(days=2),
        },
        {
            "id": "CASE-SEED-0003",
            "customer_id": "cliente2077",
            "conversation_id": None,
            "category": CaseCategory.ACCOUNT_ISSUE,
            "priority": CasePriority.NORMAL,
            "status": CaseStatus.OPEN,
            "subject": "Atualização de dados bancários",
            "description": "Open synthetic case (isolation customer).",
            "context_json": {"synthetic": True},
            "escalation_reasons": [],
            "idempotency_key": "seed:CASE-SEED-0003",
            "created_at": t - timedelta(days=1),
            "updated_at": t - timedelta(days=1),
            "sla_due_at": t + timedelta(days=1),
        },
    ]


async def seed(session: AsyncSession, now: datetime | None = None) -> dict[str, int]:
    clock = SeedClock(now or datetime.now(UTC))
    for c in CUSTOMERS:
        stmt = insert(Customer).values(**c)
        stmt = stmt.on_conflict_do_update(
            index_elements=[Customer.id], set_={k: stmt.excluded[k] for k in c if k != "id"}
        )
        await session.execute(stmt)
    # Replace backend rows of the seed customers (deterministic, repeatable).
    await session.execute(delete(Transaction).where(Transaction.customer_id.in_(SEED_CUSTOMERS)))
    await session.execute(delete(Settlement).where(Settlement.customer_id.in_(SEED_CUSTOMERS)))
    await session.execute(delete(Terminal).where(Terminal.customer_id.in_(SEED_CUSTOMERS)))
    await session.execute(delete(SupportCase).where(SupportCase.id.like("CASE-SEED-%")))
    terminals = _terminals(clock)
    await session.execute(insert(Terminal), terminals)
    await session.execute(insert(TerminalEvent), _events(clock))
    txns = _transactions(clock)
    await session.execute(insert(Transaction), txns)
    days = {str(c["id"]): int(c["settlement_days"]) for c in CUSTOMERS}  # type: ignore[call-overload]
    settlements = _settlements(clock, txns, days)
    await session.execute(insert(Settlement), settlements)
    await session.execute(insert(SupportCase), _cases(clock))
    count = (await session.execute(select(Customer.id))).all()
    return {
        "customers": len(count),
        "terminals": len(terminals),
        "transactions": len(txns),
        "settlements": len(settlements),
    }


async def _main() -> None:
    db = Database(get_settings())
    try:
        async with db.transaction() as session:
            stats = await seed(session)
        print(f"seed ok: {stats}")
    finally:
        await db.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
