"""
Production file watchdog: debounce → enqueue jobs (ADR-0012).
Replaces watchdog_stub.py. Bridges watchdog thread → asyncio via run_coroutine_threadsafe.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.config import AppConfig
from app.jobs import types as T
from app.jobs.dispatcher import JobDispatcher
from app.logging import get_logger

log = get_logger(__name__)

_WATCHED_EXTENSIONS = {".md", ".pdf", ".html"}
_PRIORITY_HIGH = 10      # 1-3 pending parse_book jobs
_PRIORITY_NIGHTLY = 1    # ≥5 pending parse_book jobs
_NIGHTLY_THRESHOLD = 5
_NIGHTLY_HOUR_UTC = 3


@dataclass
class SourceMeta:
    source_type: str   # "vault" | "book_path"
    source_id: str
    watch_path: str


class _DebouncedHandler(FileSystemEventHandler):
    """Debounces file events and enqueues jobs via asyncio."""

    def __init__(
        self,
        meta: SourceMeta,
        dispatcher: JobDispatcher,
        loop: asyncio.AbstractEventLoop,
        db_path: str,
        debounce_seconds: float,
    ) -> None:
        super().__init__()
        self._meta = meta
        self._dispatcher = dispatcher
        self._loop = loop
        self._db_path = db_path
        self._debounce_seconds = debounce_seconds
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        # For moves, watch both src and dest
        path = getattr(event, "dest_path", None) or event.src_path
        if not any(path.endswith(ext) for ext in _WATCHED_EXTENSIONS):
            return
        self._schedule(event.src_path, getattr(event, "dest_path", None), event.event_type)

    def _schedule(self, src: str, dest: str | None, event_type: str) -> None:
        key = dest or src
        with self._lock:
            if key in self._timers:
                self._timers[key].cancel()
            timer = threading.Timer(
                self._debounce_seconds,
                self._fire,
                args=(src, dest, event_type),
            )
            self._timers[key] = timer
            timer.start()

    def _fire(self, src: str, dest: str | None, event_type: str) -> None:
        with self._lock:
            self._timers.pop(dest or src, None)

        coro = self._enqueue_for_event(src, dest, event_type)
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _enqueue_for_event(
        self, src: str, dest: str | None, event_type: str
    ) -> None:
        try:
            if src.endswith(".pdf"):
                await self._handle_pdf_event(src, dest, event_type)
            elif src.endswith(".md"):
                await self._handle_note_event(src, event_type)
        except Exception as exc:
            log.error("watchdog_enqueue_error", error=str(exc), path=src)

    async def _handle_pdf_event(
        self, src: str, dest: str | None, event_type: str
    ) -> None:
        if event_type == "deleted":
            await self._dispatcher.enqueue(
                T.SOFT_DELETE_BOOK,
                {"path": src},
                priority=_PRIORITY_HIGH,
                idempotency_key=f"soft_delete:{src}",
            )
        elif event_type == "moved" and dest:
            await self._dispatcher.enqueue(
                T.UPDATE_BOOK_PATHS,
                {"old_path": src, "new_path": dest},
                priority=_PRIORITY_HIGH,
            )
        else:  # created / modified
            priority, scheduled_after = self._compute_parse_priority()
            await self._dispatcher.enqueue(
                T.PARSE_BOOK,
                {"path": src, "priority": priority},
                priority=priority,
                idempotency_key=f"parse_book:{Path(src).resolve()}",
                scheduled_after=scheduled_after,
            )

    async def _handle_note_event(self, src: str, event_type: str) -> None:
        if event_type == "deleted":
            return
        await self._dispatcher.enqueue(
            T.INDEX_NOTE,
            {"path": src, "source_type": self._meta.source_type, "source_id": self._meta.source_id},
            priority=_PRIORITY_HIGH,
            idempotency_key=f"index_note:{Path(src).resolve()}",
        )

    def _compute_parse_priority(self) -> tuple[int, str | None]:
        """Count pending parse_book jobs; assign priority and optional scheduled_after."""
        try:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            row = conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE type=? AND status='pending'",
                (T.PARSE_BOOK,),
            ).fetchone()
            conn.close()
            pending = row[0] if row else 0
        except Exception:
            pending = 0

        if pending >= _NIGHTLY_THRESHOLD:
            return _PRIORITY_NIGHTLY, _next_nightly_utc()
        return _PRIORITY_HIGH, None


class Watchdog:
    """Starts one Observer watching all vault and book_path sources."""

    def __init__(self, cfg: AppConfig, dispatcher: JobDispatcher) -> None:
        self._cfg = cfg
        self._dispatcher = dispatcher
        self._observer: Observer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        self._loop = asyncio.get_event_loop()
        sources = self._build_sources()
        if not sources:
            log.info("watchdog_no_sources")
            return

        self._observer = Observer()
        for meta in sources:
            p = Path(meta.watch_path)
            if not p.exists():
                log.warning("watchdog_path_not_found", path=meta.watch_path)
                continue
            handler = _DebouncedHandler(
                meta, self._dispatcher, self._loop, self._cfg.sqlite.path,
                self._cfg.watchdog.debounce_seconds,
            )
            self._observer.schedule(handler, str(p), recursive=True)
            log.info("watching", path=meta.watch_path, source_id=meta.source_id)

        self._observer.start()
        log.info("watchdog_started", sources=len(sources))

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()
            log.info("watchdog_stopped")

    def _build_sources(self) -> list[SourceMeta]:
        sources: list[SourceMeta] = []
        for vault in self._cfg.sources.vaults:
            sources.append(SourceMeta("vault", vault.id, str(vault.path)))
        for bp in self._cfg.sources.book_paths:
            sources.append(SourceMeta("book_path", str(bp), str(bp)))
        return sources


# ── helpers ───────────────────────────────────────────────────────────────────

def _next_nightly_utc() -> str:
    now = datetime.now(tz=timezone.utc)
    candidate = now.replace(hour=_NIGHTLY_HOUR_UTC, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.strftime("%Y-%m-%d %H:%M:%S")
