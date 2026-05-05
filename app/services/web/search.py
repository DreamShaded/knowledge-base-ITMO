"""
SearXNG search executor with SQLite result cache (ADR-0005).
Cache TTL: 24h default, 1h in fresh-mode. Idempotency key = sha256(query+engine_set).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import httpx

from app.logging import get_logger

if TYPE_CHECKING:
    from app.stores.sqlite import SQLiteStore

log = get_logger(__name__)

_DEFAULT_ENGINES = ["google", "bing", "ddg", "wikipedia", "arxiv"]
_DEFAULT_TTL_HOURS = 24
_FRESH_TTL_HOURS = 1


@dataclass
class WebSearchResult:
    url: str
    title: str
    snippet: str
    engine: str
    score: float = 1.0


def _idempotency_key(query: str, engines: list[str]) -> str:
    payload = query + "|" + ",".join(sorted(engines))
    return hashlib.sha256(payload.encode()).hexdigest()


class SearXNGSearch:
    def __init__(
        self,
        host: str = "http://localhost:8888",
        engines: list[str] | None = None,
    ) -> None:
        self._host = host.rstrip("/")
        self._engines = engines or _DEFAULT_ENGINES

    async def search(
        self,
        query: str,
        top_k: int = 5,
        sqlite: "SQLiteStore | None" = None,
        fresh_mode: bool = False,
    ) -> list[WebSearchResult]:
        """Search SearXNG, returning top-k results. Uses SQLite cache when available."""
        idem_key = _idempotency_key(query, self._engines)
        ttl_hours = _FRESH_TTL_HOURS if fresh_mode else _DEFAULT_TTL_HOURS

        if sqlite:
            cached = self._cache_get(sqlite, idem_key)
            if cached is not None:
                log.info("web_search_cached", query=query[:60], results=len(cached[:top_k]))
                return cached[:top_k]

        results = await self._fetch(query)
        if sqlite and results:
            self._cache_set(sqlite, idem_key, query, results, ttl_hours)

        log.info("web_search_done", query=query[:60], results=len(results[:top_k]), fresh_mode=fresh_mode)
        return results[:top_k]

    async def _fetch(self, query: str) -> list[WebSearchResult]:
        params = {
            "q": query,
            "format": "json",
            "engines": ",".join(self._engines),
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{self._host}/search", params=params)
                r.raise_for_status()
                data = r.json()
        except Exception as exc:
            log.warning("searxng_fetch_failed", error=str(exc), query=query[:60])
            return []

        results: list[WebSearchResult] = []
        for item in data.get("results", []):
            results.append(WebSearchResult(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=item.get("content", ""),
                engine=item.get("engine", ""),
                score=item.get("score", 1.0),
            ))
        return results

    def _cache_get(
        self, sqlite: "SQLiteStore", idem_key: str
    ) -> list[WebSearchResult] | None:
        conn: sqlite3.Connection = sqlite.connect()
        try:
            now = datetime.now(tz=timezone.utc).isoformat()
            row = conn.execute(
                "SELECT results_json FROM web_search_cache "
                "WHERE idempotency_key = ? AND expires_at > ?",
                (idem_key, now),
            ).fetchone()
            if row:
                return [WebSearchResult(**r) for r in json.loads(row[0])]
            return None
        finally:
            conn.close()

    def _cache_set(
        self,
        sqlite: "SQLiteStore",
        idem_key: str,
        query: str,
        results: list[WebSearchResult],
        ttl_hours: int,
    ) -> None:
        conn: sqlite3.Connection = sqlite.connect()
        now = datetime.now(tz=timezone.utc)
        expires = (now + timedelta(hours=ttl_hours)).isoformat()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO web_search_cache "
                "(idempotency_key, query, results_json, fetched_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    idem_key,
                    query,
                    json.dumps([r.__dict__ for r in results]),
                    now.isoformat(),
                    expires,
                ),
            )
            conn.commit()
        finally:
            conn.close()
