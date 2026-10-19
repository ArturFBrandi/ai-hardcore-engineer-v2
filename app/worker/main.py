"""Background worker (same image as the API): outbox delivery, HITL SLA timer, retention purge.

Each job is a pure function of (db, clock) so tests drive them with an injected clock — no sleeps.
Multiple worker replicas are safe: rows are claimed with ``FOR UPDATE SKIP LOCKED``.
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from prometheus_client import start_http_server
from sqlalchemy import select

from app.config import Settings, get_settings
from app.db.base import Database
from app.db.models import ActorType, CasePriority, CaseStatus, Outbox, OutboxStatus, SupportCase, TurnRole
from app.db.repositories import write_audit
from app.llm.reliability import backoff_delay
from app.memory.store import MemoryStore
from app.observability import metrics
from app.observability.logging import configure_logging, get_logger
from app.orchestration.decision import sla_minutes

log = get_logger(__name__)
Clock = Callable[[], datetime]
BUMP = {
    CasePriority.LOW: CasePriority.NORMAL,
    CasePriority.NORMAL: CasePriority.HIGH,
    CasePriority.HIGH: CasePriority.URGENT,
    CasePriority.URGENT: CasePriority.URGENT,
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Sink:
    """Operator notification sink: Teams/Slack-compatible webhook, or a structured log line by default."""

    def __init__(self, webhook_url: str, client: httpx.AsyncClient | None = None) -> None:
        self.url = webhook_url
        self.client = client or httpx.AsyncClient(timeout=10.0)

    async def deliver(self, topic: str, payload: dict[str, Any]) -> None:
        text = f"[{topic}] case {payload.get('case_id')} · {payload.get('priority')} · {payload.get('category', '')} · {payload.get('subject', payload.get('reason', ''))}"
        if not self.url:
            log.info(
                "operator_notification",
                topic=topic,
                case_id=payload.get("case_id"),
                priority=payload.get("priority"),
                sink="log",
            )
            return
        resp = await self.client.post(self.url, json={"text": text, "topic": topic, "payload": payload})
        resp.raise_for_status()


async def deliver_outbox(
    db: Database, sink: Sink, settings: Settings, clock: Clock = utcnow, batch: int = 20
) -> dict[str, int]:
    stats = {"delivered": 0, "retried": 0, "dead": 0}
    async with db.transaction() as s:
        rows = (
            await s.scalars(
                select(Outbox)
                .where(Outbox.status == OutboxStatus.PENDING, Outbox.next_attempt_at <= clock())
                .order_by(Outbox.id)
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            try:
                await sink.deliver(row.topic, row.payload_json)
            except (httpx.HTTPError, OSError) as exc:
                row.attempts += 1
                row.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                if row.attempts >= settings.outbox_max_attempts:
                    row.status = OutboxStatus.DEAD
                    stats["dead"] += 1
                    metrics.OUTBOX_DELIVERIES.labels(status="dead").inc()
                    log.error("outbox_dead_letter", outbox_id=row.id, topic=row.topic, attempts=row.attempts)
                else:
                    row.next_attempt_at = clock() + timedelta(
                        seconds=max(1.0, backoff_delay(row.attempts, base=2.0, cap=300.0))
                    )
                    stats["retried"] += 1
                    metrics.OUTBOX_DELIVERIES.labels(status="retry").inc()
                continue
            row.status = OutboxStatus.DELIVERED
            row.attempts += 1
            row.delivered_at = clock()
            stats["delivered"] += 1
            metrics.OUTBOX_DELIVERIES.labels(status="delivered").inc()
    return stats


async def check_sla(
    db: Database, memory: MemoryStore, settings: Settings, clock: Clock = utcnow, publisher: Any = None
) -> list[str]:
    """No operator action before ``sla_due_at`` → bump priority, notify again (outbox), tell the customer."""
    breached: list[str] = []
    now = clock()
    async with db.transaction() as s:
        cases = (
            await s.scalars(
                select(SupportCase)
                .where(
                    SupportCase.awaiting_human.is_(True),
                    SupportCase.status.in_((CaseStatus.OPEN, CaseStatus.IN_PROGRESS)),
                    SupportCase.sla_due_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        for case in cases:
            before = {
                "priority": case.priority.value,
                "sla_due_at": case.sla_due_at.isoformat() if case.sla_due_at else None,
            }
            case.priority = BUMP[case.priority]
            case.sla_breaches += 1
            case.sla_due_at = now + timedelta(minutes=sla_minutes(case.priority.value, settings.hitl_sla_minutes))
            case.updated_at = now
            s.add(
                Outbox(
                    topic="case.sla_breached",
                    status=OutboxStatus.PENDING,
                    next_attempt_at=now,
                    payload_json={
                        "case_id": case.id,
                        "priority": case.priority.value,
                        "reason": f"SLA breach #{case.sla_breaches}",
                    },
                )
            )
            await write_audit(
                s,
                actor_type=ActorType.SYSTEM,
                actor_id="worker",
                action="case.sla_breached",
                entity="support_case",
                entity_id=case.id,
                request_id=None,
                before=before,
                after={"priority": case.priority.value, "sla_breaches": case.sla_breaches},
            )
            breached.append(case.id)
            metrics.HITL_SLA_BREACHES.inc()
    for case_id in breached:
        async with db.session() as s:
            case = await s.get(SupportCase, case_id)
        if case is None or not case.conversation_id:
            continue
        conv = case.conversation_id
        lang = (case.context_json or {}).get("language", "pt")
        text = (
            f"Seu atendimento {case_id} continua na fila com prioridade elevada. Um especialista responderá aqui assim que possível."
            if lang == "pt"
            else f"Your case {case_id} is still in the queue and its priority was raised. A specialist will reply here as soon as possible."
        )
        await memory.append_turn(
            conv, turn=0, role=TurnRole.SYSTEM_NOTE, content=text, case_id=case_id, status="sla_update"
        )
        if publisher is not None:
            await publisher.publish(conv, {"type": "case_status", "case_id": case_id, "content": text})
    return breached


async def purge_expired(memory: MemoryStore, checkpointer: Any, clock: Clock = utcnow) -> int:
    """Retention: delete expired conversations (turns cascade) and their graph checkpoints."""
    expired = await memory.expired(clock())
    for conv_id, customer_id in expired:
        if checkpointer is not None:
            await checkpointer.adelete_thread(f"{customer_id}:{conv_id}")
    return await memory.delete_ids([c for c, _ in expired])


async def refresh_queue_gauge(db: Database) -> None:
    from sqlalchemy import func  # noqa: PLC0415 - tiny helper import kept local

    async with db.session() as s:
        n = await s.scalar(select(func.count()).select_from(SupportCase).where(SupportCase.awaiting_human.is_(True)))
    metrics.HITL_QUEUE.set(int(n or 0))


async def run_forever(settings: Settings) -> None:
    from app.container import RedisPublisher, make_checkpointer  # noqa: PLC0415 - avoid importing the graph at module import
    from redis.asyncio import Redis  # noqa: PLC0415

    db = Database(settings)
    memory = MemoryStore(db, settings.conversation_ttl_days, settings.conversation_lock_timeout_seconds)
    sink = Sink(settings.operator_webhook_url)
    checkpointer, pool = await make_checkpointer(settings)
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    publisher = RedisPublisher(redis)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    last_purge = datetime.min.replace(tzinfo=UTC)
    log.info("worker_started", webhook="configured" if settings.operator_webhook_url else "log_sink")
    try:
        while not stop.is_set():
            try:
                stats = await deliver_outbox(db, sink, settings)
                breached = await check_sla(db, memory, settings, publisher=publisher)
                await refresh_queue_gauge(db)
                if stats["delivered"] or stats["retried"] or stats["dead"] or breached:
                    log.info("worker_tick", **stats, sla_breached=len(breached))
                if utcnow() - last_purge > timedelta(hours=1):
                    purged = await purge_expired(memory, checkpointer)
                    last_purge = utcnow()
                    if purged:
                        log.info("retention_purge", conversations=purged)
            except Exception as exc:  # noqa: BLE001 - the worker must survive transient datastore errors
                log.error("worker_tick_failed", error=type(exc).__name__, detail=str(exc)[:200])
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
            except TimeoutError:
                pass
    finally:
        await pool.close()
        await redis.aclose()
        await db.dispose()
        log.info("worker_stopped")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    start_http_server(9100)  # worker metrics for Prometheus
    asyncio.run(run_forever(settings))


if __name__ == "__main__":
    main()
