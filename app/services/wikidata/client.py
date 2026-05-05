"""
WikidataClient: HTTP adapter for wbsearchentities + SPARQL endpoint (ADR-0013 §3).
CircuitBreaker: 3 failures → open for 5 min (per ADR-0013).
Privacy: only QIDs and surface labels sent — no note text (PRD §7.3).
"""
from __future__ import annotations

from typing import Any

import httpx

from app.governance.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from app.logging import get_logger

log = get_logger(__name__)

_API_URL = "https://www.wikidata.org/w/api.php"
_SPARQL_URL = "https://query.wikidata.org/sparql"
_UA = "knowledge-base/0.1.0 (contact: dreamshaded@gmail.com)"

# Module-level CB: one instance per Wikidata service (singleton per import)
_CB = CircuitBreaker(name="wikidata", failure_threshold=3, recovery_timeout=300.0)


async def search_entities(surface: str, limit: int = 5) -> list[dict[str, Any]]:
    """
    Search wbsearchentities in ru then en, dedup by QID.
    Returns [] silently when circuit is open.
    """
    seen: dict[str, dict] = {}
    for lang in ("ru", "en"):
        try:
            hits = await _CB.call(lambda: _do_search(surface, lang, limit))
            for item in hits:
                qid = item.get("id", "")
                if qid and qid not in seen:
                    seen[qid] = item
        except CircuitBreakerOpen:
            log.warning("wikidata_cb_open", op="search", surface=surface)
            break
        except Exception as exc:
            log.warning("wikidata_search_error", surface=surface, lang=lang, error=str(exc))
    return list(seen.values())[:limit]


async def sparql_query(query: str) -> list[dict[str, Any]] | None:
    """
    Execute SPARQL SELECT. Returns raw binding dicts:
    [{varname: {"type": ..., "value": ..., "xml:lang": ...}}].
    Returns None when circuit is OPEN (caller must not cache empty result).
    Returns [] when query succeeds but matches nothing.
    """
    try:
        return await _CB.call(lambda: _do_sparql(query))
    except CircuitBreakerOpen:
        log.warning("wikidata_cb_open", op="sparql")
        return None  # None = CB open; [] = valid empty result
    except Exception as exc:
        log.warning("wikidata_sparql_error", error=str(exc))
        return []


async def _do_search(surface: str, lang: str, limit: int) -> list[dict]:
    params = {
        "action": "wbsearchentities",
        "search": surface,
        "language": lang,
        "limit": limit,
        "format": "json",
        "type": "item",
    }
    async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": _UA}) as c:
        r = await c.get(_API_URL, params=params)
        r.raise_for_status()
    return r.json().get("search", [])


async def _do_sparql(query: str) -> list[dict[str, Any]]:
    headers = {"User-Agent": _UA, "Accept": "application/sparql-results+json"}
    async with httpx.AsyncClient(timeout=30.0, headers=headers) as c:
        r = await c.get(_SPARQL_URL, params={"query": query, "format": "json"})
        r.raise_for_status()
    return r.json().get("results", {}).get("bindings", [])
