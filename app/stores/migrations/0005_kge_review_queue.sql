-- Phase-12: KGE review queue and run tracking.
-- Predictions themselves live in Oxigraph <urn:predictions>; this table tracks
-- review state (approve / reject / defer) and surfacing metadata.

CREATE TABLE IF NOT EXISTS kge_review_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT    NOT NULL,
    head_uri        TEXT    NOT NULL,
    predicted_rel   TEXT    NOT NULL,
    tail_uri        TEXT    NOT NULL,
    score           REAL    NOT NULL,
    zscore          REAL    NOT NULL DEFAULT 0.0,
    head_label      TEXT    NOT NULL DEFAULT '',
    tail_label      TEXT    NOT NULL DEFAULT '',
    justification   TEXT    NOT NULL DEFAULT '',   -- "shared concepts [X, Y]"
    entity_type     TEXT    NOT NULL DEFAULT '',   -- note|concept|tag|wikidata
    status          TEXT    NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|deferred
    defer_until     TEXT,                           -- ISO-8601 UTC; set when deferred
    reject_reason   TEXT,
    reviewed_at     TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    surfaced_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_kge_queue_status
    ON kge_review_queue(status, defer_until);

CREATE INDEX IF NOT EXISTS idx_kge_queue_run
    ON kge_review_queue(run_id);

CREATE TABLE IF NOT EXISTS kge_runs (
    run_id      TEXT    PRIMARY KEY,
    mrr         REAL    NOT NULL DEFAULT 0.0,
    hits_at_10  REAL    NOT NULL DEFAULT 0.0,
    num_triples INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
