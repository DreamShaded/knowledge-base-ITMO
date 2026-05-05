"""
Full /save-web implementation (phase-07, supersedes app/services/web_saved.py).
Idempotency: same URL + same content → no-op. Changed content → new sha + previous_versions link.
Auto-enqueues index_web_saved job after save.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from app.logging import get_logger
from app.services.web.extraction import ExtractionResult, extract_page

if TYPE_CHECKING:
    from app.jobs.dispatcher import JobDispatcher
    from app.stores.sqlite import SQLiteStore

log = get_logger(__name__)


@dataclass
class SaveWebResult:
    url: str
    content_hash: str
    page_path: Path
    is_duplicate: bool        # same content as existing entry
    is_update: bool           # same URL, changed content
    previous_hashes: list[str]


async def save_web_page(
    url: str,
    data_dir: Path,
    sqlite: "SQLiteStore",
    dispatcher: "JobDispatcher | None" = None,
    gpu_mutex=None,
    playwright_priority: int = 3,
) -> SaveWebResult:
    """
    Fetch, extract, and save a web page to data/web-saved/<content_sha256>/page.md.
    Enqueues index_web_saved job if dispatcher is available.
    """
    extraction = await extract_page(
        url,
        sqlite=sqlite,
        gpu_mutex=gpu_mutex,
        playwright_priority=playwright_priority,
    )
    if not extraction.content_md or extraction.extraction_method == "snippet":
        raise ValueError(f"Could not extract meaningful content from {url}")

    content_hash = extraction.content_hash
    previous_hashes = _get_previous_hashes(sqlite, url, content_hash)

    # Check idempotency: same URL + same content hash
    out_dir = data_dir / "web-saved" / content_hash
    page_path = out_dir / "page.md"

    if page_path.exists() and not _is_update(sqlite, url, content_hash):
        log.info("save_web_duplicate", url=url, hash=content_hash[:12])
        return SaveWebResult(
            url=url,
            content_hash=content_hash,
            page_path=page_path,
            is_duplicate=True,
            is_update=False,
            previous_hashes=previous_hashes,
        )

    is_update = bool(previous_hashes)

    # Write page.md
    out_dir.mkdir(parents=True, exist_ok=True)
    fetched_at = extraction.fetched_at
    title = extraction.title or url
    frontmatter = _build_frontmatter(url, fetched_at, title, content_hash, previous_hashes)
    page_path.write_text(frontmatter + extraction.content_md, encoding="utf-8")

    # Update URL → hash index
    _update_index(sqlite, url, content_hash, fetched_at, previous_hashes)

    log.info(
        "save_web_done",
        url=url,
        hash=content_hash[:12],
        is_update=is_update,
        method=extraction.extraction_method,
    )

    # Auto-enqueue indexing
    if dispatcher:
        from app.jobs import types as T
        await dispatcher.enqueue(
            T.INDEX_WEB_SAVED,
            {
                "url_hash": content_hash,
                "content": extraction.content_md,
                "vault_id": "_web_saved",
                "modified_at": fetched_at,
            },
        )

    return SaveWebResult(
        url=url,
        content_hash=content_hash,
        page_path=page_path,
        is_duplicate=False,
        is_update=is_update,
        previous_hashes=previous_hashes,
    )


# ── helpers ───────────────────────────────────────────────────────────────────

def _build_frontmatter(
    url: str,
    fetched_at: str,
    title: str,
    content_hash: str,
    previous_hashes: list[str],
) -> str:
    lines = [
        "---",
        f"url: {url}",
        f"fetched_at: {fetched_at}",
        f"title: {title!r}",
        f"content_hash: {content_hash}",
        "vault_id: _web_saved",
    ]
    if previous_hashes:
        prev_yaml = json.dumps(previous_hashes)
        lines.append(f"previous_versions: {prev_yaml}")
    lines.append("---\n\n")
    return "\n".join(lines)


def _get_previous_hashes(sqlite: "SQLiteStore", url: str, new_hash: str) -> list[str]:
    """Return list of previous content hashes for this URL (excluding new_hash)."""
    conn: sqlite3.Connection = sqlite.connect()
    try:
        row = conn.execute(
            "SELECT content_hash, previous_hashes_json FROM web_saved_index WHERE url = ?",
            (url,),
        ).fetchone()
        if not row:
            return []
        current_hash, prev_json = row[0], row[1]
        if current_hash == new_hash:
            return []  # same content — no previous versions to add
        prev = json.loads(prev_json or "[]")
        if current_hash not in prev:
            prev.append(current_hash)
        return prev
    finally:
        conn.close()


def _is_update(sqlite: "SQLiteStore", url: str, new_hash: str) -> bool:
    """True if URL exists in index with a DIFFERENT content hash."""
    conn: sqlite3.Connection = sqlite.connect()
    try:
        row = conn.execute(
            "SELECT content_hash FROM web_saved_index WHERE url = ?", (url,)
        ).fetchone()
        return row is not None and row[0] != new_hash
    finally:
        conn.close()


def _update_index(
    sqlite: "SQLiteStore",
    url: str,
    content_hash: str,
    fetched_at: str,
    previous_hashes: list[str],
) -> None:
    conn: sqlite3.Connection = sqlite.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO web_saved_index "
            "(url, content_hash, fetched_at, previous_hashes_json) VALUES (?, ?, ?, ?)",
            (url, content_hash, fetched_at, json.dumps(previous_hashes)),
        )
        conn.commit()
    finally:
        conn.close()
