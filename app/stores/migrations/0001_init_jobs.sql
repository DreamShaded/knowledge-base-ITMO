-- Phase-02: full jobs schema with idempotency, all app tables.
-- Drops phase-01 stub jobs table (schema was incomplete).

DROP TABLE IF EXISTS idempotency;
DROP TABLE IF EXISTS jobs;

CREATE TABLE jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    type            TEXT    NOT NULL,
    payload_json    TEXT    NOT NULL DEFAULT '{}',
    priority        INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    started_at      TEXT,
    finished_at     TEXT,
    error           TEXT,
    scheduled_after TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_runnable
    ON jobs(priority DESC, created_at ASC)
    WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS idempotency (
    key    TEXT    PRIMARY KEY,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS web_cache (
    url_hash   TEXT PRIMARY KEY,
    url        TEXT NOT NULL,
    content    TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS faithfulness_cache (
    cache_key  TEXT PRIMARY KEY,
    result     TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qdrant_schema (
    collection_name         TEXT PRIMARY KEY,
    embed_model_id          TEXT,
    mrl_dim                 INTEGER,
    sparse_modifier         TEXT,
    instruct_template_hash  TEXT,
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    created_by_app_version  TEXT
);
