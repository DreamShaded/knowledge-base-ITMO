"""
/save-web idempotency tests (ADR-0005).
Same URL + same content → no-op.
Same URL + changed content → new hash + previous_versions.
New URL → creates file normally.
"""
from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


def _load_handler():
    p = Path(__file__).parent.parent.parent / "app" / "services" / "web" / "save-web-handler.py"
    name = "_save_web_handler_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture
def handler():
    return _load_handler()


class _FakeSQLite:
    """
    SQLite store backed by a named shared-cache in-memory DB.
    Keeps a long-lived keeper connection so the in-memory DB persists
    across multiple connect()/close() cycles from callers.
    """

    def __init__(self) -> None:
        db_name = uuid.uuid4().hex
        self._uri = f"file:{db_name}?mode=memory&cache=shared"
        # Keeper keeps the DB alive for the lifetime of this object
        self._keeper = sqlite3.connect(self._uri, uri=True, check_same_thread=False)
        self._keeper.executescript("""
            CREATE TABLE IF NOT EXISTS web_saved_index (
                url TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                previous_hashes_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS web_page_cache (
                url_hash TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                content_md TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                extraction_method TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
        """)
        self._keeper.commit()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn


def _fake_extraction(url: str, content: str):
    from app.services.web.extraction import ExtractionResult
    return ExtractionResult(
        url=url,
        title="Test Page",
        content_md=content,
        fetched_at="2026-04-29T00:00:00+00:00",
        extraction_method="trafilatura",
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
    )


def _patch_extract(extraction):
    """Patch extract_page in the handler module (where it's imported, not where it's defined)."""
    return patch.object(
        sys.modules["_save_web_handler_test"],
        "extract_page",
        new=AsyncMock(return_value=extraction),
    )


@pytest.mark.asyncio
async def test_new_url_creates_file(handler, tmp_path):
    url = "https://example.com/article"
    content = "This is the article content. " * 30
    extraction = _fake_extraction(url, content)
    sqlite = _FakeSQLite()

    with _patch_extract(extraction):
        result = await handler.save_web_page(url, tmp_path, sqlite, dispatcher=None)

    assert not result.is_duplicate
    assert not result.is_update
    assert result.page_path.exists()
    assert result.content_hash == extraction.content_hash
    text = result.page_path.read_text(encoding="utf-8")
    assert url in text
    assert "web_saved" in text


@pytest.mark.asyncio
async def test_same_content_is_no_op(handler, tmp_path):
    url = "https://example.com/article"
    content = "Same content here. " * 30
    extraction = _fake_extraction(url, content)
    sqlite = _FakeSQLite()

    with _patch_extract(extraction):
        first = await handler.save_web_page(url, tmp_path, sqlite, dispatcher=None)
        second = await handler.save_web_page(url, tmp_path, sqlite, dispatcher=None)

    assert not first.is_duplicate
    assert second.is_duplicate
    assert first.content_hash == second.content_hash
    assert second.page_path == first.page_path


@pytest.mark.asyncio
async def test_changed_content_creates_new_version(handler, tmp_path):
    url = "https://example.com/article"
    content_v1 = "Version 1 content. " * 30
    content_v2 = "Version 2 updated content. " * 30
    sqlite = _FakeSQLite()

    with _patch_extract(_fake_extraction(url, content_v1)):
        first = await handler.save_web_page(url, tmp_path, sqlite, dispatcher=None)

    with _patch_extract(_fake_extraction(url, content_v2)):
        second = await handler.save_web_page(url, tmp_path, sqlite, dispatcher=None)

    assert not second.is_duplicate
    assert second.is_update
    assert second.content_hash != first.content_hash
    assert first.content_hash in second.previous_hashes
    text = second.page_path.read_text(encoding="utf-8")
    assert first.content_hash in text


@pytest.mark.asyncio
async def test_enqueues_index_job(handler, tmp_path):
    url = "https://example.com/new"
    content = "Content to index. " * 30
    extraction = _fake_extraction(url, content)
    sqlite = _FakeSQLite()

    enqueued: list[tuple] = []

    class _FakeDispatcher:
        def enqueue(self, job_type, payload):
            enqueued.append((job_type, payload))

    with _patch_extract(extraction):
        await handler.save_web_page(url, tmp_path, sqlite, dispatcher=_FakeDispatcher())

    assert len(enqueued) == 1
    job_type, payload = enqueued[0]
    assert job_type == "index_web_saved"
    assert payload["content"] == content
    assert payload["vault_id"] == "_web_saved"
