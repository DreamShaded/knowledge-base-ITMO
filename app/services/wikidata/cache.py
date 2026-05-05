"""
SQLite-backed cache for Wikidata fetch results.
TTL: L1/L2 → 30 days, L3 → 7 days.
Caller provides a connection factory to avoid owning a long-lived connection.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.logging import get_logger

log = get_logger(__name__)

_TTL_DAYS = {"L1": 30, "L2": 30, "L3": 7}


class WikidataCache:
    def __init__(self, conn_factory: Callable[[], sqlite3.Connection]) -> None:
        self._conn_factory = conn_factory

    def get(self, qid: str, level: str) -> Any | None:
        """Return cached payload or None if missing/expired."""
        conn = self._conn_factory()
        try:
            row = conn.execute(
                "SELECT payload FROM wikidata_cache WHERE qid=? AND level=? AND expires_at>?",
                (qid, level, _now()),
            ).fetchone()
            return json.loads(row["payload"]) if row else None
        except Exception as exc:
            log.warning("wikidata_cache_get_error", qid=qid, level=level, error=str(exc))
            return None
        finally:
            conn.close()

    def set(self, qid: str, level: str, payload: Any) -> None:
        """Upsert payload with TTL-based expiry."""
        ttl = _TTL_DAYS.get(level, 30)
        expires = (datetime.now(timezone.utc) + timedelta(days=ttl)).isoformat()
        conn = self._conn_factory()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO wikidata_cache (qid, level, payload, fetched_at, expires_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (qid, level, json.dumps(payload), _now(), expires),
            )
            conn.commit()
        except Exception as exc:
            log.warning("wikidata_cache_set_error", qid=qid, level=level, error=str(exc))
        finally:
            conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
