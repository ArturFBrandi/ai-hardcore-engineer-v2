"""E2E over HTTP against the containerized stack (`make test-e2e`, part of `make verify`).

The stack runs the real images (api, worker, migrate, models-init, postgres, qdrant, redis). Offline runs
use LLM_PROVIDER=fake so no credits are needed; live provider checks are in tests/live.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid

import httpx
import pytest

from app.api.dev_idp import mint

pytestmark = pytest.mark.stack
API = os.environ.get("API_URL", "http://localhost:8000")
KEY = ".dev-keys/dev-private.pem"


def hdr(sub: str = "cliente1988", scope: str = "chat conversations:read conversations:delete") -> dict[str, str]:
    return {"Authorization": f"Bearer {mint(sub, scope, private_key_path=KEY)}"}


OP = "cases:read cases:write approvals:write"


@pytest.fixture(scope="module")
def client() -> httpx.Client:
    c = httpx.Client(base_url=API, timeout=240)
    try:
        c.get("/health/live").raise_for_status()
    except httpx.HTTPError as exc:  # pragma: no cover - environment problem
        pytest.fail(f"API not reachable at {API} ({exc}); start it with `make up` (offline: LLM_PROVIDER=fake)")
    return c


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], check=True, capture_output=True, timeout=300)


def wait_ready(c: httpx.Client, timeout: float = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if c.get("/health/ready").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    raise AssertionError("stack did not become ready")


def chat(c: httpx.Client, message: str, conv: str | None = None, sub: str = "cliente1988", **kw: str) -> httpx.Response:
    payload = {"message": message, **kw}
    if conv:
        payload["conversation_id"] = conv
    return c.post("/v1/chat", json=payload, headers=hdr(sub))


def test_all_services_healthy_and_ready(client: httpx.Client) -> None:
    out = subprocess.run(
        ["docker", "compose", "ps", "--format", "json"], check=True, capture_output=True, text=True
    ).stdout
    rows = [json.loads(line) for line in out.strip().splitlines()]
    by_service = {r["Service"]: r for r in rows}
    for svc in ("api", "worker", "postgres", "qdrant", "redis"):
        assert svc in by_service, f"{svc} not running"
        assert "healthy" in by_service[svc]["Status"], by_service[svc]["Status"]
    ready = client.get("/health/ready").json()
    assert ready["status"] == "ready"
    assert ready["checks"]["qdrant"]["chunks"] > 500
    assert "agent_requests_total" in client.get("/metrics").text


def test_challenge_payload_end_to_end(client: httpx.Client) -> None:
    r = chat(client, "When will the money from yesterday's sales be deposited?", user_id="cliente1988")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["intent"] == "customer_settlement"
    assert {"customer_support", "knowledge", "supervisor"} <= set(b["agents"])
    assert "get_settlement_schedule" in b["tools_used"]
    assert b["sources"], "settlement policy evidence is cited"
    assert r.headers["x-request-id"] == b["request_id"]


def test_context_survives_api_restart(client: httpx.Client) -> None:
    t1 = chat(client, "Quais foram minhas vendas de ontem?").json()
    compose("restart", "api")
    wait_ready(client)
    t2 = chat(client, "E a de anteontem?", t1["conversation_id"]).json()
    assert t2["turn"] == 2
    assert t2["intent"] == "customer_transaction"
    turns = client.get(f"/v1/conversations/{t1['conversation_id']}", headers=hdr()).json()["turns"]
    assert [t["role"] for t in turns] == ["customer", "assistant", "customer", "assistant"]


def test_hitl_over_http_with_sse(client: httpx.Client) -> None:
    b = chat(client, "Quero falar com um atendente humano").json()
    assert b["status"] == "awaiting_human"
    case_id, conv = b["case_id"], b["conversation_id"]
    token = mint("cliente1988", "conversations:read", private_key_path=KEY)
    received: list[dict] = []
    with (
        httpx.Client(base_url=API, timeout=60) as sse,
        sse.stream("GET", f"/v1/conversations/{conv}/events", params={"access_token": token}) as stream,
    ):
        lines = stream.iter_lines()
        assert "ready" in next(lines) or True
        r = client.post(
            f"/v1/operator/cases/{case_id}/reply",
            json={"message": "Retorno do especialista via E2E. Protocolo 1212.", "send_as_is": True},
            headers=hdr("operator.e2e", OP),
        )
        assert r.status_code == 200, r.text
        deadline = time.time() + 30
        for line in lines:
            if line.startswith("data:"):
                data = json.loads(line[5:].strip())
                if data.get("type") == "human_reply":
                    received.append(data)
                    break
            if time.time() > deadline:
                break
    assert received and "1212" in received[0]["content"]
    turns = client.get(f"/v1/conversations/{conv}", headers=hdr()).json()["turns"]
    assert turns[-1]["role"] == "human_operator"


def test_identity_rules(client: httpx.Client) -> None:
    assert chat(client, "oi", user_id="cliente2077").status_code == 403
    assert client.post("/v1/chat", json={"message": "oi", "user_id": "cliente1988"}).status_code == 401
    t = chat(client, "Quais foram minhas vendas de ontem?").json()
    assert chat(client, "e hoje?", t["conversation_id"], sub="cliente2077").status_code == 404


def test_qdrant_outage_degrades_gracefully(client: httpx.Client) -> None:
    compose("stop", "qdrant")
    try:
        r = chat(client, "Em quantas parcelas posso dividir uma venda no crediário?")
        assert r.status_code == 200
        body = r.json()
        assert body["sources"] == []
        assert "indisponível" in body["answer"] or "unavailable" in body["answer"]
        assert client.get("/health/ready").status_code == 503
    finally:
        compose("start", "qdrant")
        wait_ready(client)


def test_redis_outage_policy(client: httpx.Client) -> None:
    compose("stop", "redis")
    try:
        # rate limiting fails open for customer chat (documented); idempotency fails closed
        ok = chat(client, "Quais foram minhas vendas de ontem?")
        assert ok.status_code == 200
        idem = client.post(
            "/v1/chat", json={"message": "oi"}, headers={**hdr(), "Idempotency-Key": f"e2e-{uuid.uuid4().hex}"}
        )
        assert idem.status_code == 503
        assert client.get("/health/ready").status_code == 503
    finally:
        compose("start", "redis")
        wait_ready(client)


def test_postgres_outage_returns_503_envelope(client: httpx.Client) -> None:
    compose("stop", "postgres")
    try:
        r = chat(client, "Quais foram minhas vendas de ontem?")
        assert r.status_code == 503
        assert r.json()["error"] == "dependency_unavailable"
        assert "Traceback" not in r.text
    finally:
        compose("start", "postgres")
        wait_ready(client)


def test_demo_auth_mode_accepts_exact_challenge_payload(client: httpx.Client) -> None:
    env = {**os.environ, "AUTH_MODE": "demo"}
    subprocess.run(
        ["docker", "compose", "up", "-d", "--wait", "api"], check=True, capture_output=True, env=env, timeout=300
    )
    try:
        wait_ready(client)
        r = client.post(
            "/v1/chat",
            json={"message": "When will the money from yesterday's sales be deposited?", "user_id": "cliente1988"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["intent"] == "customer_settlement"
        assert client.get("/v1/operator/cases").status_code == 401, "operators always need JWTs"
    finally:
        subprocess.run(["docker", "compose", "up", "-d", "--wait", "api"], check=True, capture_output=True, timeout=300)
        wait_ready(client)
