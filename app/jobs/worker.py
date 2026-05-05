"""
Single-writer async job worker (ADR-0011).
One asyncio task; claims one pending job at a time, retries with exp backoff.
Uses a single persistent SQLite connection for all worker DB operations.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.governance.gpu_mutex import GpuMutex
from app.jobs.dispatcher import JobDispatcher
from app.logging import get_logger

log = get_logger(__name__)

# Retry delay in seconds per attempt index (0-based): 1s, 5s, 30s → then fail
_RETRY_DELAYS = [1, 5, 30]
_POLL_INTERVAL = 1.0  # seconds between polls when queue is empty


@dataclass
class WorkerContext:
    db_path: str
    data_dir: Path
    gpu_mutex: GpuMutex
    dispatcher: JobDispatcher
    container: object = None  # set to AppContainer after build; avoids circular import


class JobWorker:
    def __init__(self, ctx: WorkerContext) -> None:
        self._ctx = ctx
        self._task: asyncio.Task | None = None
        self._running = False
        self._conn: sqlite3.Connection | None = None

    def _open_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._ctx.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @property
    def _db(self) -> sqlite3.Connection:
        """Return the persistent worker connection, re-opening if needed."""
        if self._conn is None:
            self._conn = self._open_conn()
        return self._conn

    def start(self) -> None:
        self._conn = self._open_conn()
        self._reset_stale_running()
        self._running = True
        self._task = asyncio.get_event_loop().create_task(
            self._run(), name="job-worker"
        )
        log.info("worker_started")

    def _reset_stale_running(self) -> None:
        """Reset jobs stuck in 'running' from a previous crash back to 'pending'."""
        cur = self._db.execute(
            "UPDATE jobs SET status='pending', started_at=NULL WHERE status='running'"
        )
        self._db.commit()
        if cur.rowcount:
            log.warning("worker_reset_stale_running", count=cur.rowcount)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._conn:
            self._conn.close()
            self._conn = None
        log.info("worker_stopped")

    # ── main loop ─────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        while self._running:
            try:
                job = self._claim_job()
                if job is None:
                    await asyncio.sleep(_POLL_INTERVAL)
                    continue
                await self._process(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.error("worker_loop_error", error=str(exc))
                await asyncio.sleep(_POLL_INTERVAL)

    def _claim_job(self) -> dict | None:
        """Atomically claim the highest-priority pending job. Returns row dict or None."""
        self._db.execute("""
            UPDATE jobs
            SET status='running', started_at=datetime('now'), attempts=attempts+1
            WHERE id = (
                SELECT id FROM jobs
                WHERE status='pending'
                  AND (scheduled_after IS NULL OR scheduled_after <= datetime('now'))
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
            )
        """)
        self._db.commit()
        row = self._db.execute(
            "SELECT * FROM jobs WHERE status='running' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    async def _process(self, job: dict) -> None:
        import time as _time
        job_id = job["id"]
        job_type = job["type"]
        payload = json.loads(job["payload_json"])
        attempts = job["attempts"]
        _t = _time.monotonic()

        path = payload.get("path") or payload.get("url") or ""
        log.info("job_started", job_id=job_id, job_type=job_type, attempt=attempts, path=path)

        try:
            await self._dispatch(job_type, payload)
            self._complete(job_id)
            log.info(
                "job_completed",
                job_id=job_id,
                job_type=job_type,
                path=path,
                latency_ms=int((_time.monotonic() - _t) * 1000),
            )
        except Exception as exc:
            import traceback as _tb
            error_msg = f"{type(exc).__name__}: {exc}"
            log.error(
                "job_failed",
                job_id=job_id,
                job_type=job_type,
                path=path,
                error=error_msg,
                traceback=_tb.format_exc(),
                attempt=attempts,
                latency_ms=int((_time.monotonic() - _t) * 1000),
            )
            self._fail_or_retry(job_id, attempts, error_msg)

    async def _dispatch(self, job_type: str, payload: dict) -> None:
        from app.jobs.job_handlers import dispatch_job
        await dispatch_job(job_type, payload, self._ctx)

    def _complete(self, job_id: int) -> None:
        self._db.execute(
            "UPDATE jobs SET status='done', finished_at=datetime('now'), error=NULL WHERE id=?",
            (job_id,),
        )
        self._db.commit()

    def _fail_or_retry(self, job_id: int, attempts: int, error: str) -> None:
        if attempts >= len(_RETRY_DELAYS) + 1:
            self._db.execute(
                "UPDATE jobs SET status='failed', finished_at=datetime('now'), error=? WHERE id=?",
                (error, job_id),
            )
        else:
            delay = _RETRY_DELAYS[min(attempts - 1, len(_RETRY_DELAYS) - 1)]
            retry_after = (
                datetime.now(tz=timezone.utc) + timedelta(seconds=delay)
            ).strftime("%Y-%m-%d %H:%M:%S")
            self._db.execute(
                "UPDATE jobs SET status='pending', error=?, scheduled_after=? WHERE id=?",
                (error, retry_after, job_id),
            )
        self._db.commit()
