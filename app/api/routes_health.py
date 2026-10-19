"""Liveness, readiness and Prometheus metrics endpoints (no auth by design)."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "alive"}
