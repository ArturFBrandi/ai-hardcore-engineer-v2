"""POST /v1/chat — the challenge endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from app.api.auth import Principal, enforce_body_identity, require_scopes
from app.api.schemas import ChatRequest, ChatResponse, ErrorResponse
from app.observability.tracing import current_ids
from app.services.chat import TurnResult

router = APIRouter(tags=["chat"])
AGENT_ORDER = ("router", "customer_support", "knowledge", "supervisor", "synthesizer", "escalation")


def build_response(result: TurnResult, request_id: str, trace_id: str) -> dict[str, Any]:
    s = result.state
    ran = {e.get("agent") for e in s.get("events") or []}
    agents = [a for a in AGENT_ORDER if a in ran]
    tools = list(
        dict.fromkeys(
            t["tool"]
            for t in s.get("tool_results") or []
            if t.get("status") in ("ok", "not_found", "approval_required")
        )
    )
    status = s.get("status", "answered")
    return ChatResponse(
        request_id=request_id,
        trace_id=trace_id,
        conversation_id=result.conversation_id,
        turn=result.turn,
        status=status,
        answer=s.get("final_answer", ""),
        intent=s.get("intent", "unknown"),
        agents=agents,
        route_source=s.get("route_source", "unknown"),
        confidence=float(s.get("confidence", 1.0) or 0.0),
        escalated=status in ("awaiting_human", "escalated"),
        escalation_reasons=list(s.get("escalation_reasons") or []),
        case_id=s.get("case_id"),
        sources=[
            {"title": c["title"], "url": c["url"], "category": c["category"], "retrieved_at": c.get("retrieved_at")}
            for c in s.get("citations") or []
        ],
        language=s.get("language", "pt"),
        plan_steps=[f"{p['id']}:{p['agent']}" for p in (s.get("plan") or {}).get("steps", [])],
        tools_used=tools,
        token_usage={k: v for k, v in (s.get("token_usage") or {}).items() if k in ("llm_calls", "total", "usd")},
    ).model_dump()


@router.post(
    "/v1/chat",
    response_model=ChatResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def chat(
    body: ChatRequest,
    request: Request,
    principal: Principal = require_scopes("chat", allow_demo=True),
    idempotency_key: str | None = Header(
        default=None, alias="Idempotency-Key", max_length=128, pattern=r"^[A-Za-z0-9_.:-]{8,128}$"
    ),
) -> Any:
    enforce_body_identity(principal, body.user_id)
    container = request.app.state.container
    idem = request.app.state.idempotency
    request_id: str = request.state.request_id
    trace_id, _ = current_ids()
    fingerprint = idem.fingerprint(body.model_dump())
    if idempotency_key:
        replay = await idem.begin(principal.subject, idempotency_key, fingerprint)
        if replay is not None:
            return JSONResponse(replay, headers={"Idempotent-Replayed": "true"})
    try:
        result = await container.chat.run_turn(
            principal.auth_context(request_id), body.message, body.conversation_id, request_id, trace_id
        )
    except BaseException:
        if idempotency_key:
            await idem.abort(principal.subject, idempotency_key)
        raise
    response = build_response(result, request_id, trace_id)
    if idempotency_key:
        await idem.complete(principal.subject, idempotency_key, fingerprint, response)
    return response
