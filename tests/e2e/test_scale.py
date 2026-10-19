"""Horizontal scaling: two API replicas behind nginx (`make scale-test`).

Alternating replicas must keep conversation context (PostgreSQL), shared rate limits (Redis) and debug
traces (Redis). The proxy adds `X-Upstream` so the test can prove both replicas served the conversation.
"""

from __future__ import annotations

import os
import uuid

import httpx
import pytest

from app.api.dev_idp import mint

pytestmark = [pytest.mark.stack, pytest.mark.scale]
API = os.environ.get("API_URL", "http://localhost:8080")


def hdr(sub: str, scope: str = "chat conversations:read conversations:delete") -> dict[str, str]:
    return {"Authorization": f"Bearer {mint(sub, scope, private_key_path='.dev-keys/dev-private.pem')}"}


def test_two_replicas_share_context_rate_limits_and_traces() -> None:
    with httpx.Client(base_url=API, timeout=240) as c:
        upstreams: set[str] = set()
        conv = None
        messages = ["Quais foram minhas vendas de ontem?", "E a de anteontem?", "E hoje?", "E nos últimos 7 dias?"]
        request_ids = []
        for i, m in enumerate(messages, 1):
            payload = {"message": m, **({"conversation_id": conv} if conv else {})}
            r = c.post("/v1/chat", json=payload, headers=hdr("cliente1988"))
            assert r.status_code == 200, r.text
            upstreams.add(r.headers.get("x-upstream", ""))
            body = r.json()
            conv = body["conversation_id"]
            assert body["turn"] == i, "turn numbering is consistent across replicas"
            request_ids.append(body["request_id"])
        assert len(upstreams) >= 2, f"both replicas must serve the conversation (saw {upstreams})"

        # Debug traces written by one replica are readable from the other (Redis trace store).
        op = hdr("operator.scale", "cases:read")
        assert all(c.get(f"/v1/debug/traces/{rid}", headers=op).status_code == 200 for rid in request_ids)

        # Rate limits are shared: the per-subject budget is consumed across replicas.
        sub = f"rl-{uuid.uuid4().hex[:6]}"
        h = hdr(sub)
        codes = [c.get("/v1/conversations", headers=h).status_code for _ in range(40)]
        assert 429 in codes, "the shared limiter must trip even when requests alternate replicas"
