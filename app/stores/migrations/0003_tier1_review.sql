-- Phase-10: Tier-1 review metadata (deferred decisions, rejection log).
-- Triples themselves live in Oxigraph named graphs; this table tracks review state.

CREATE TABLE IF NOT EXISTS tier1_review (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    triple_hash  TEXT    NOT NULL UNIQUE,   -- SHA-256 of "<subj>|<pred>|<obj>"
    subject_uri  TEXT    NOT NULL,
    predicate    TEXT    NOT NULL,
    object_value TEXT    NOT NULL,          -- URI or literal
    is_literal   INTEGER NOT NULL DEFAULT 0,
    confidence   REAL    NOT NULL,
    conflict     INTEGER NOT NULL DEFAULT 0,
    chunk_id     TEXT    NOT NULL,
    doc_uri      TEXT    NOT NULL DEFAULT '',
    status       TEXT    NOT NULL DEFAULT 'pending',  -- pending|deferred|approved|rejected
    defer_until  TEXT,                       -- ISO-8601 UTC; set when status=deferred
    reject_reason TEXT,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    decided_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_tier1_review_status
    ON tier1_review(status, defer_until);
