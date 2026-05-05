"""Faithfulness result cache backed by SQLite faithfulness_cache table (ADR-0011). TTL 7 days."""
from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from app.logging import get_logger
from app.services.faithfulness.schemas import FaithfulnessResult

if TYPE_CHECKING:
    from app.stores.sqlite import SQLiteStore

log = get_logger(__name__)

_TTL_DAYS = 7


class FaithfulnessCache:
    def __init__(self, sqlite: "SQLiteStore", ttl_days: int = _TTL_DAYS) -> None:
        self._sqlite = sqlite
        self._ttl = timedelta(days=ttl_days)

    async def get(self, canonical: str) -> FaithfulnessResult | None:
        """Non-blocking cache lookup; SQLite I/O runs in thread pool."""
        return await asyncio.to_thread(self._get_sync, canonical)

    async def set(self, canonical: str, result: FaithfulnessResult) -> None:
        """Non-blocking cache write; SQLite I/O runs in thread pool."""
        await asyncio.to_thread(self._set_sync, canonical, result)

    async def purge_expired(self) -> int:
        """Non-blocking purge; SQLite I/O runs in thread pool."""
        return await asyncio.to_thread(self._purge_sync)

    # ── sync implementations run via to_thread ────────────────────────────────

    def _get_sync(self, canonical: str) -> FaithfulnessResult | None:
        key = _sha256(canonical)
        now = datetime.now(tz=timezone.utc).isoformat()
        conn = self._sqlite.connect()
        try:
            row = conn.execute(
                "SELECT result FROM faithfulness_cache WHERE cache_key=? AND expires_at>?",
                (key, now),
            ).fetchone()
            if row is None:
                return None
            result = FaithfulnessResult.model_validate_json(row["result"])
            log.debug("faithfulness_cache_hit", key=key[:12])
            return result.model_copy(update={"from_cache": True})
        finally:
            conn.close()

    def _set_sync(self, canonical: str, result: FaithfulnessResult) -> None:
        key = _sha256(canonical)
        now = datetime.now(tz=timezone.utc)
        expires = (now + self._ttl).isoformat()
        conn = self._sqlite.connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO faithfulness_cache (cache_key, result, created_at, expires_at)"
                " VALUES (?, ?, ?, ?)",
                (key, result.model_dump_json(), now.isoformat(), expires),
            )
            conn.commit()
            log.debug("faithfulness_cache_set", key=key[:12])
        finally:
            conn.close()

    def _purge_sync(self) -> int:
        now = datetime.now(tz=timezone.utc).isoformat()
        conn = self._sqlite.connect()
        try:
            cur = conn.execute("DELETE FROM faithfulness_cache WHERE expires_at<=?", (now,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
