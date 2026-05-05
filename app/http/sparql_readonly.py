"""
/v1/sparql — read-only SPARQL endpoint (SELECT/ASK/DESCRIBE).

GET  ?query=<sparql>
POST application/x-www-form-urlencoded  query=<sparql>

Limits: SELECT/ASK/DESCRIBE only · 30 s timeout · 10 000 row cap.
Full implementation added in phase-04.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/v1", tags=["sparql"])

_HERE = Path(__file__).parent.parent / "services" / "graph"


def _load_executor():
    name = "_sparql_executor"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / "sparql-executor.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@router.get("/sparql")
async def sparql_get(request: Request, query: str = "") -> JSONResponse:
    return await _execute(request, query)


@router.post("/sparql")
async def sparql_post(request: Request) -> JSONResponse:
    form = await request.form()
    query = str(form.get("query", ""))
    return await _execute(request, query)


async def _execute(request: Request, query: str) -> JSONResponse:
    if not query.strip():
        raise HTTPException(status_code=400, detail="Missing SPARQL query")

    executor = _load_executor()

    try:
        executor.validate_query(query)
    except executor.SPARQLForbiddenError as exc:
        raise HTTPException(status_code=403, detail=str(exc))

    store = request.app.state.container.oxigraph
    try:
        result = await executor.execute(store, query)
    except executor.SPARQLTimeoutError as exc:
        raise HTTPException(status_code=408, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return JSONResponse(result)
