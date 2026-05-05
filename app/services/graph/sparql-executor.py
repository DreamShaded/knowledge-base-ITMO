"""Async SPARQL executor with query-type guard, row limit, and timeout.

Wraps OxigraphStore (synchronous) via asyncio ThreadPoolExecutor.
Allowed query types: SELECT, ASK, DESCRIBE (read-only per ADR-0008).
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.stores.oxigraph import OxigraphStore

_MAX_ROWS = 10_000
_TIMEOUT_S = 30.0
_ALLOWED_PREFIXES = ("SELECT", "ASK", "DESCRIBE")

# Shared executor: SPARQL queries are CPU-bound but short; 2 threads suffice
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sparql")


class SPARQLError(Exception):
    pass


class SPARQLForbiddenError(SPARQLError):
    pass


class SPARQLTimeoutError(SPARQLError):
    pass


def validate_query(query: str) -> None:
    """Raise SPARQLForbiddenError if query is not SELECT/ASK/DESCRIBE."""
    stripped = query.strip().upper()
    for line in stripped.splitlines():
        line = line.strip()
        if not line or line.startswith("PREFIX") or line.startswith("#"):
            continue
        # First non-comment, non-prefix line determines query type
        if any(line.startswith(p) for p in _ALLOWED_PREFIXES):
            return
        raise SPARQLForbiddenError(
            f"Only {'/'.join(_ALLOWED_PREFIXES)} queries allowed"
        )
    # All lines were PREFIX/comments — no query body found
    raise SPARQLForbiddenError("Empty or PREFIX-only query not allowed")


async def execute(
    store: "OxigraphStore",
    query: str,
    max_rows: int = _MAX_ROWS,
    timeout_s: float = _TIMEOUT_S,
) -> dict[str, Any]:
    """Execute SPARQL query and return normalized result dict.

    Returns:
        {"type": "select", "rows": [...], "count": N}  for SELECT
        {"type": "ask",    "result": bool}              for ASK
        {"type": "describe","triples": [...]}            for DESCRIBE
    """
    validate_query(query)

    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.wait_for(
            loop.run_in_executor(_EXECUTOR, _run_query, store, query, max_rows),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        raise SPARQLTimeoutError(f"Query exceeded {timeout_s}s timeout")
    return result


def _run_query(store: "OxigraphStore", query: str, max_rows: int) -> dict[str, Any]:
    """Synchronous query execution (runs in thread pool)."""
    import pyoxigraph as px

    raw = store.store.query(query)

    if isinstance(raw, px.QueryBoolean):
        return {"type": "ask", "result": bool(raw)}

    if isinstance(raw, px.QueryTriples):
        triples = []
        for triple in raw:
            triples.append({
                "s": triple.subject.value,
                "p": triple.predicate.value,
                "o": _term_value(triple.object),
            })
            if len(triples) >= max_rows:
                break
        return {"type": "describe", "triples": triples}

    # QuerySolutions (SELECT)
    variables = [v.value for v in raw.variables]
    rows: list[dict] = []
    for sol in raw:
        row = {}
        for i, var in enumerate(variables):
            term = sol[i]
            row[var] = _term_value(term) if term is not None else None
        rows.append(row)
        if len(rows) >= max_rows:
            break
    return {"type": "select", "rows": rows, "count": len(rows), "variables": variables}


def _term_value(term) -> str:
    """Return human-readable value: IRI for NamedNode, plain for Literal."""
    if term is None:
        return ""
    return term.value
