"""Daily web request quota (PRD §11 п.9). Counter in SQLite, resets on UTC midnight."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.logging import get_logger

if TYPE_CHECKING:
    from app.stores.sqlite import SQLiteStore

log = get_logger(__name__)


class DailyQuota:
    def __init__(self, limit: int = 50) -> None:
        self._limit = limit

    def check_and_increment(self, sqlite: "SQLiteStore") -> bool:
        """Returns True if quota allows this request (and increments counter)."""
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        conn: sqlite3.Connection = sqlite.connect()
        try:
            conn.execute(
                "INSERT INTO daily_quota (date, count) VALUES (?, 1) "
                "ON CONFLICT(date) DO UPDATE SET count = count + 1",
                (today,),
            )
            conn.commit()
            row = conn.execute(
                "SELECT count FROM daily_quota WHERE date = ?", (today,)
            ).fetchone()
            count = row[0] if row else 1
            if count > self._limit:
                log.warning("web_quota_exceeded", date=today, count=count, limit=self._limit)
                return False
            return True
        finally:
            conn.close()

    def remaining(self, sqlite: "SQLiteStore") -> int:
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        conn: sqlite3.Connection = sqlite.connect()
        try:
            row = conn.execute(
                "SELECT count FROM daily_quota WHERE date = ?", (today,)
            ).fetchone()
            used = row[0] if row else 0
            return max(0, self._limit - used)
        finally:
            conn.close()
