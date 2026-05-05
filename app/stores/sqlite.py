"""
SQLite store: WAL mode, migration runner, health check (ADR-0011).
Single-writer pattern — only JobWorker writes; reads are unrestricted.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from app.logging import get_logger

log = get_logger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


@dataclass
class SQLiteStore:
    path: str
    _migrations_dir: Path = field(default=_MIGRATIONS_DIR, init=False, repr=False)

    def init(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        conn.execute("PRAGMA journal_mode=WAL")
        self._apply_migrations(conn)
        conn.close()
        log.info("initialized", path=self.path)

    def health(self) -> dict:
        try:
            conn = self._connect()
            conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
            conn.close()
            return {"status": "ok"}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    def connect(self) -> sqlite3.Connection:
        return self._connect()

    # ── internal ──────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    def _apply_migrations(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL UNIQUE,
                applied_at TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.commit()

        applied = {
            row[0]
            for row in conn.execute("SELECT name FROM schema_migrations").fetchall()
        }

        for sql_path in sorted(self._migrations_dir.glob("*.sql")):
            name = sql_path.stem
            if name in applied:
                continue
            log.info("migration_applying", name=name)
            conn.executescript(sql_path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (name,))
            conn.commit()
            log.info("migration_done", name=name)
