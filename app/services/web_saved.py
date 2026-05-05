"""
Initial /save-web implementation (phase-02 scope).
HTTP fetch → trafilatura extract → data/web-saved/<sha256>/page.md
Full implementation (Playwright fallback, idempotency) deferred to phase-07.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.logging import get_logger

log = get_logger(__name__)


async def save_web_page(url: str, data_dir: Path) -> Path:
    """
    Fetch URL, extract readable text with trafilatura, write to data/web-saved/<sha256>/page.md.
    Returns path to the saved page.
    """
    html = await _fetch(url)
    text = _extract(html, url)
    if not text:
        raise ValueError(f"trafilatura extracted no content from {url}")

    sha256 = hashlib.sha256(url.encode()).hexdigest()
    out_dir = data_dir / "web-saved" / sha256
    out_dir.mkdir(parents=True, exist_ok=True)

    fetched_at = datetime.now(tz=timezone.utc).isoformat()
    title = _extract_title(html) or url

    frontmatter = (
        "---\n"
        f"url: {url}\n"
        f"fetched_at: {fetched_at}\n"
        f"title: {title}\n"
        "---\n\n"
    )
    page_path = out_dir / "page.md"
    page_path.write_text(frontmatter + text, encoding="utf-8")
    log.info("web_page_saved", url=url, sha256=sha256[:12], chars=len(text))
    return page_path


# ── internal ──────────────────────────────────────────────────────────────────

async def _fetch(url: str) -> str:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (knowledge-base-bot)"})
        r.raise_for_status()
        return r.text


def _extract(html: str, url: str) -> str:
    try:
        import trafilatura  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "trafilatura not installed — run: uv sync --extra web"
        ) from exc
    result = trafilatura.extract(html, url=url, include_formatting=True)
    return result or ""


def _extract_title(html: str) -> str:
    import re
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    return m.group(1).strip() if m else ""
