"""
Web page content extraction (ADR-0005).
Priority: trafilatura → Playwright fallback (JS-heavy / short result) → snippet fallback.
Results cached in web_page_cache (7d).
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

_MIN_TRAFILATURA_LEN = 200
_PAGE_CACHE_TTL_DAYS = 7


@dataclass
class ExtractionResult:
    url: str
    title: str
    content_md: str
    fetched_at: str
    extraction_method: str   # "trafilatura" | "playwright" | "snippet"
    content_hash: str


async def extract_page(
    url: str,
    snippet: str = "",
    sqlite: "SQLiteStore | None" = None,
    gpu_mutex=None,
    playwright_priority: int = 3,
) -> ExtractionResult:
    """Extract readable content from a URL. Returns ExtractionResult."""
    url_hash = hashlib.sha256(url.encode()).hexdigest()

    if sqlite:
        cached = _cache_get(sqlite, url_hash)
        if cached is not None:
            return cached

    result = await _do_extract(url, snippet, gpu_mutex, playwright_priority)

    if sqlite and result.content_md:
        _cache_set(sqlite, url_hash, result)

    return result


async def _do_extract(
    url: str,
    snippet: str,
    gpu_mutex,
    playwright_priority: int,
) -> ExtractionResult:
    fetched_at = datetime.now(tz=timezone.utc).isoformat()

    # Primary: trafilatura
    try:
        html = await _http_fetch(url)
        content, title = _trafilatura_extract(html, url)
        if content and len(content) >= _MIN_TRAFILATURA_LEN:
            return ExtractionResult(
                url=url,
                title=title,
                content_md=content,
                fetched_at=fetched_at,
                extraction_method="trafilatura",
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
            )
        # Short result: try Playwright
        pw_content, pw_title = await _playwright_extract(url, gpu_mutex, playwright_priority)
        if pw_content and len(pw_content) >= _MIN_TRAFILATURA_LEN:
            return ExtractionResult(
                url=url,
                title=pw_title or title,
                content_md=pw_content,
                fetched_at=fetched_at,
                extraction_method="playwright",
                content_hash=hashlib.sha256(pw_content.encode()).hexdigest(),
            )
    except Exception as exc:
        log.warning("extraction_primary_failed", url=url[:80], error=str(exc))

    # Snippet fallback
    fallback = snippet or f"[No content extracted from {url}]"
    return ExtractionResult(
        url=url,
        title="",
        content_md=fallback,
        fetched_at=fetched_at,
        extraction_method="snippet",
        content_hash=hashlib.sha256(fallback.encode()).hexdigest(),
    )


async def _http_fetch(url: str) -> str:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (knowledge-base-bot)"})
        r.raise_for_status()
        return r.text


def _trafilatura_extract(html: str, url: str) -> tuple[str, str]:
    try:
        import trafilatura  # type: ignore[import]
    except ImportError:
        return "", ""
    content = trafilatura.extract(html, url=url, include_formatting=True) or ""
    # Extract title from metadata
    try:
        meta = trafilatura.extract_metadata(html)
        title = (meta.title or "") if meta else ""
    except Exception:
        title = ""
    return content, title


async def _playwright_extract(
    url: str, gpu_mutex, playwright_priority: int
) -> tuple[str, str]:
    """Headless Playwright extraction for JS-heavy sites."""
    try:
        from playwright.async_api import async_playwright  # type: ignore[import]
    except ImportError:
        return "", ""

    ctx_mgr = gpu_mutex.acquire(priority=playwright_priority) if gpu_mutex else None
    try:
        if ctx_mgr:
            async with ctx_mgr:
                return await _playwright_run(url)
        return await _playwright_run(url)
    except Exception as exc:
        log.warning("playwright_extract_failed", url=url[:80], error=str(exc))
        return "", ""


async def _playwright_run(url: str) -> tuple[str, str]:
    from playwright.async_api import async_playwright  # type: ignore[import]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        title = await page.title()
        html = await page.content()
        await browser.close()
    content, _ = _trafilatura_extract(html, url)
    return content, title


def _cache_get(sqlite: "SQLiteStore", url_hash: str) -> ExtractionResult | None:
    conn: sqlite3.Connection = sqlite.connect()
    try:
        now = datetime.now(tz=timezone.utc).isoformat()
        row = conn.execute(
            "SELECT url, content_md, title, extraction_method, content_hash, fetched_at "
            "FROM web_page_cache WHERE url_hash = ? AND expires_at > ?",
            (url_hash, now),
        ).fetchone()
        if row:
            return ExtractionResult(
                url=row[0],
                content_md=row[1],
                title=row[2],
                extraction_method=row[3],
                content_hash=row[4],
                fetched_at=row[5],
            )
        return None
    finally:
        conn.close()


def _cache_set(sqlite: "SQLiteStore", url_hash: str, result: ExtractionResult) -> None:
    conn: sqlite3.Connection = sqlite.connect()
    now = datetime.now(tz=timezone.utc)
    expires = (now + timedelta(days=_PAGE_CACHE_TTL_DAYS)).isoformat()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO web_page_cache "
            "(url_hash, url, content_md, title, extraction_method, content_hash, fetched_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                url_hash,
                result.url,
                result.content_md,
                result.title,
                result.extraction_method,
                result.content_hash,
                result.fetched_at,
                expires,
            ),
        )
        conn.commit()
    finally:
        conn.close()
