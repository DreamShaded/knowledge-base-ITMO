"""Unit tests for JobDispatcher: enqueue, idempotency dedup, priority."""
import pytest

from app.jobs.dispatcher import JobDispatcher
from app.stores.sqlite import SQLiteStore


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "test.db")
    store = SQLiteStore(path=path)
    store.init()
    return store


@pytest.fixture
def dispatcher(db):
    return JobDispatcher(db)


# ── basic enqueue ─────────────────────────────────────────────────────────────

async def test_enqueue_returns_job_id(dispatcher):
    job_id = await dispatcher.enqueue("parse_book", {"path": "/tmp/a.pdf"})
    assert isinstance(job_id, int)
    assert job_id > 0


async def test_enqueue_stores_type_and_payload(dispatcher, db):
    import json, sqlite3
    await dispatcher.enqueue("index_note", {"path": "/vault/note.md"}, priority=5)
    conn = sqlite3.connect(db.path)
    row = conn.execute("SELECT type, payload_json, priority FROM jobs WHERE id=1").fetchone()
    conn.close()
    assert row[0] == "index_note"
    assert json.loads(row[1]) == {"path": "/vault/note.md"}
    assert row[2] == 5


async def test_enqueue_default_status_is_pending(dispatcher, db):
    import sqlite3
    await dispatcher.enqueue("parse_book", {})
    conn = sqlite3.connect(db.path)
    row = conn.execute("SELECT status FROM jobs WHERE id=1").fetchone()
    conn.close()
    assert row[0] == "pending"


async def test_enqueue_sets_scheduled_after(dispatcher, db):
    import sqlite3
    await dispatcher.enqueue("parse_book", {}, scheduled_after="2099-01-01 03:00:00")
    conn = sqlite3.connect(db.path)
    row = conn.execute("SELECT scheduled_after FROM jobs WHERE id=1").fetchone()
    conn.close()
    assert row[0] == "2099-01-01 03:00:00"


# ── idempotency ───────────────────────────────────────────────────────────────

async def test_idempotency_returns_same_job_id(dispatcher):
    key = "parse_book:/books/foo.pdf"
    id1 = await dispatcher.enqueue("parse_book", {"path": "/books/foo.pdf"}, idempotency_key=key)
    id2 = await dispatcher.enqueue("parse_book", {"path": "/books/foo.pdf"}, idempotency_key=key)
    assert id1 == id2


async def test_idempotency_creates_only_one_row(dispatcher, db):
    import sqlite3
    key = "parse_book:/books/bar.pdf"
    for _ in range(3):
        await dispatcher.enqueue("parse_book", {}, idempotency_key=key)
    conn = sqlite3.connect(db.path)
    count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    assert count == 1


async def test_no_idempotency_key_allows_duplicates(dispatcher, db):
    import sqlite3
    for _ in range(3):
        await dispatcher.enqueue("parse_book", {"path": "/books/baz.pdf"})
    conn = sqlite3.connect(db.path)
    count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    assert count == 3


async def test_different_keys_create_separate_jobs(dispatcher, db):
    import sqlite3
    await dispatcher.enqueue("parse_book", {}, idempotency_key="key-a")
    await dispatcher.enqueue("parse_book", {}, idempotency_key="key-b")
    conn = sqlite3.connect(db.path)
    count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    conn.close()
    assert count == 2


# ── priority ordering ─────────────────────────────────────────────────────────

async def test_enqueue_priority_stored_correctly(dispatcher, db):
    import sqlite3
    await dispatcher.enqueue("parse_book", {}, priority=10)
    await dispatcher.enqueue("index_note", {}, priority=1)
    conn = sqlite3.connect(db.path)
    rows = conn.execute(
        "SELECT priority FROM jobs ORDER BY priority DESC"
    ).fetchall()
    conn.close()
    assert rows[0][0] == 10
    assert rows[1][0] == 1


# ── schema: attempts starts at 0 ─────────────────────────────────────────────

async def test_new_job_has_zero_attempts(dispatcher, db):
    import sqlite3
    await dispatcher.enqueue("embed_chunks", {})
    conn = sqlite3.connect(db.path)
    row = conn.execute("SELECT attempts FROM jobs WHERE id=1").fetchone()
    conn.close()
    assert row[0] == 0
