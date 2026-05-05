"""
/healthz (liveness) and /readyz (readiness) endpoints.
/readyz checks all three stores; returns 503 on any failure.
"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def liveness() -> dict:
    return {"status": "ok"}


@router.get("/readyz")
async def readiness(request: Request, response: Response) -> dict:
    container = request.app.state.container

    checks = {
        "qdrant":    container.qdrant.health(),
        "oxigraph":  container.oxigraph.health(),
        "sqlite":    container.sqlite.health(),
    }

    failed = [name for name, result in checks.items() if result.get("status") != "ok"]
    if failed:
        response.status_code = 503
        return {"status": "degraded", "failed": failed, "checks": checks}

    return {"status": "ok", "checks": checks}
