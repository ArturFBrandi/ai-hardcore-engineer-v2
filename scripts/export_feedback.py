"""`make export-feedback`: turn operator resolutions and replies into CANDIDATE evaluation cases.

Output: ``evaluation/feedback/candidates-<date>.json`` — each case carries the customer question, the
escalation reasons, the operator's (redacted) answer and ``status: needs_human_review``. Nothing is added
to the gated datasets automatically: a human reviews and promotes cases into ``dataset.json`` / ``golden``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.db.base import Database
from app.db.models import ConversationTurn, SupportCase, TurnRole
from app.guardrails.redaction import redact_text

OUT_DIR = Path("evaluation/feedback")


async def main() -> None:
    db = Database(get_settings())
    out = []
    try:
        async with db.session() as s:
            cases = (await s.scalars(select(SupportCase).where(SupportCase.conversation_id.is_not(None)))).all()
            for case in cases:
                turns = (
                    await s.scalars(
                        select(ConversationTurn)
                        .where(ConversationTurn.conversation_id == case.conversation_id)
                        .order_by(ConversationTurn.id)
                    )
                ).all()
                question = next((t.content for t in turns if t.role is TurnRole.CUSTOMER), None)
                answer = next((t.content for t in turns if t.role is TurnRole.HUMAN_OPERATOR), None)
                if not question or not (answer or case.resolution_code):
                    continue
                out.append(
                    {
                        "id": f"feedback-{case.id}",
                        "question": redact_text(question),
                        "operator_answer": redact_text(answer or ""),
                        "resolution_code": case.resolution_code,
                        "category": case.category.value,
                        "escalation_reasons": case.escalation_reasons,
                        "suggested_expectation": {"expected_escalation": True},
                        "status": "needs_human_review",
                    }
                )
    finally:
        await db.dispose()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"candidates-{datetime.now(UTC):%Y%m%d}.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(out)} candidate cases to {path} (review before promoting)")


if __name__ == "__main__":
    asyncio.run(main())
