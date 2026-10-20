"""Operator (human-in-the-loop) endpoints. Operator scopes only; customers' tokens are rejected (403)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query, Request

from app.api.auth import Principal, require_scopes
from app.api.schemas import OperatorDecisionIn, OperatorReplyIn, OperatorResolveIn

router = APIRouter(prefix="/v1/operator", tags=["operator"])


@router.get("/cases")
async def list_cases(
    request: Request,
    status: Literal["open", "in_progress", "awaiting_customer", "resolved", "closed"] | None = Query(default=None),
    priority: Literal["low", "normal", "high", "urgent"] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: Principal = require_scopes("cases:read"),
) -> dict[str, Any]:
    items = await request.app.state.hitl.queue(status, priority, limit=limit + 1, offset=offset)
    return {"items": items[:limit], "next_offset": offset + limit if len(items) > limit else None}


@router.get("/cases/{case_id}")
async def get_case(
    case_id: str, request: Request, principal: Principal = require_scopes("cases:read")
) -> dict[str, Any]:
    detail: dict[str, Any] = await request.app.state.hitl.detail(case_id)
    return detail


@router.post("/cases/{case_id}/claim")
async def claim_case(
    case_id: str, request: Request, principal: Principal = require_scopes("cases:write")
) -> dict[str, Any]:
    out: dict[str, Any] = await request.app.state.hitl.claim(case_id, principal.subject, request.state.request_id)
    return out


@router.post("/cases/{case_id}/reply")
async def reply_case(
    case_id: str, body: OperatorReplyIn, request: Request, principal: Principal = require_scopes("cases:write")
) -> dict[str, Any]:
    out: dict[str, Any] = await request.app.state.hitl.reply(
        case_id, principal.subject, body.message, body.send_as_is, request.state.request_id
    )
    return out


@router.post("/cases/{case_id}/decision")
async def decide_case(
    case_id: str, body: OperatorDecisionIn, request: Request, principal: Principal = require_scopes("approvals:write")
) -> dict[str, Any]:
    out: dict[str, Any] = await request.app.state.hitl.decide(
        case_id, principal.subject, body.decision, body.edited_arguments, body.note, request.state.request_id
    )
    return out


@router.post("/cases/{case_id}/resolve")
async def resolve_case(
    case_id: str, body: OperatorResolveIn, request: Request, principal: Principal = require_scopes("cases:write")
) -> dict[str, Any]:
    out: dict[str, Any] = await request.app.state.hitl.resolve(
        case_id, principal.subject, body.resolution_code, body.note, request.state.request_id
    )
    return out
