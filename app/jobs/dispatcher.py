"""
Job dispatcher: async enqueue with idempotency dedup (ADR-0011).
Uses aiosqlite so callers can enqueue from async context (watchdog, HTTP handlers).
"""
from __future__ import annotations

import asyncio
import json

import aiosqlite

from app.logging import get_logger
from app.stores.sqlite import SQLiteStore

log = get_logger(__name__)


class JobDispatcher:
    def __init__(self, store: SQLiteStore) -> None:
        self._path = store.path

    async def enqueue(
        self,
        job_type: str,
        payload: dict,
        priority: int = 0,
        idempotency_key: str | None = None,
        scheduled_after: str | None = None,
    ) -> int:
        """
        Enqueue a job. Returns existing job_id if idempotency_key already present.
        scheduled_after: ISO-8601 UTC datetime; job won't run until then.
        """
        for attempt in range(4):
            try:
                async with aiosqlite.connect(self._path) as db:
                    await db.execute("PRAGMA foreign_keys=ON")
                    await db.execute("PRAGMA busy_timeout=10000")

                    if idempotency_key:
                        row = await (
                            await db.execute(
                                "SELECT job_id FROM idempotency WHERE key = ?",
                                (idempotency_key,),
                            )
                        ).fetchone()
                        if row:
                            log.debug("enqueue_dedup", key=idempotency_key, job_id=row[0])
                            return row[0]

                    cursor = await db.execute(
                        """
                        INSERT INTO jobs (type, payload_json, priority, scheduled_after)
                        VALUES (?, ?, ?, ?)
                        """,
                        (job_type, json.dumps(payload), priority, scheduled_after),
                    )
                    job_id = cursor.lastrowid

                    if idempotency_key:
                        await db.execute(
                            "INSERT OR IGNORE INTO idempotency (key, job_id) VALUES (?, ?)",
                            (idempotency_key, job_id),
                        )

                    await db.commit()

                log.info("enqueued", job_type=job_type, job_id=job_id, priority=priority)
                return job_id
            except Exception as exc:
                if "unable to open database file" in str(exc) and attempt < 3:
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                raise
