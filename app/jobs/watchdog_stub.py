"""
Watchdog stub (ADR-0012, phase-01).
Observes all vault paths and book_paths with 1 s debounce.
Phase-01: logs events only — no enqueue. Job dispatch added in phase-02.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from app.config import AppConfig
from app.logging import get_logger

log = get_logger(__name__)

_DEBOUNCE_SECONDS = 1.0
_WATCHED_EXTENSIONS = {".md", ".pdf", ".html"}


@dataclass
class SourceMeta:
    source_type: str      # "vault" | "book_path" | "web_saved"
    source_id: str        # vault_id or path string
    watch_path: str


class _DebouncedHandler(FileSystemEventHandler):
    """File event handler that logs events after a 1 s debounce window."""

    def __init__(self, meta: SourceMeta) -> None:
        super().__init__()
        self._meta = meta
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = getattr(event, "dest_path", None) or event.src_path
        if not any(path.endswith(ext) for ext in _WATCHED_EXTENSIONS):
            return
        self._schedule(path, event.event_type)

    def _schedule(self, path: str, event_type: str) -> None:
        with self._lock:
            if path in self._timers:
                self._timers[path].cancel()
            timer = threading.Timer(
                _DEBOUNCE_SECONDS,
                self._fire,
                args=(path, event_type),
            )
            self._timers[path] = timer
            timer.start()

    def _fire(self, path: str, event_type: str) -> None:
        with self._lock:
            self._timers.pop(path, None)
        log.info(
            "file_changed",
            event_type=event_type,
            path=path,
            source_type=self._meta.source_type,
            source_id=self._meta.source_id,
            note="enqueue_not_yet_implemented",
        )


class WatchdogStub:
    """Starts one Observer with N watched paths from config. Call start() once."""

    def __init__(self, cfg: AppConfig) -> None:
        self._cfg = cfg
        self._observer: Observer | None = None

    def start(self) -> None:
        sources = self._build_sources()
        if not sources:
            log.info("no_sources_configured")
            return

        self._observer = Observer()
        for meta in sources:
            p = Path(meta.watch_path)
            if not p.exists():
                log.warning("path_not_found", path=meta.watch_path)
                continue
            handler = _DebouncedHandler(meta)
            self._observer.schedule(handler, str(p), recursive=True)
            log.info("watching", path=meta.watch_path, source_id=meta.source_id)

        self._observer.start()
        log.info("started", source_count=len(sources))

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join()
            log.info("stopped")

    def _build_sources(self) -> list[SourceMeta]:
        sources: list[SourceMeta] = []
        for vault in self._cfg.sources.vaults:
            sources.append(SourceMeta("vault", vault.id, str(vault.path)))
        for bp in self._cfg.sources.book_paths:
            sources.append(SourceMeta("book_path", str(bp), str(bp)))
        return sources
