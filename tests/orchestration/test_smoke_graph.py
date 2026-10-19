import uuid

from app.tools.context import AuthContext


async def test_settlement_workflow_runs(container, fake_llm) -> None:
    auth = AuthContext(customer_id="cliente1988", subject="cliente1988", scopes=frozenset({"chat"}))
    r = await container.chat.run_turn(
        auth, "When will the money from yesterday's sales be deposited?", None, f"req_{uuid.uuid4().hex[:8]}", "t" * 32
    )
    s = r.state
    print(s.get("intent"), s.get("route"), s.get("route_source"), s.get("status"), s.get("confidence"))
    print(s.get("final_answer"))
    for e in s.get("events", []):
        print(e["agent"], e["event"], e.get("started_at"), e.get("latency_ms"), str(e.get("detail"))[:150])
    assert s["intent"] == "customer_settlement"
