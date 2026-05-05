-- Phase-13: GraphDelta tracker + training log for adaptive retrain (ADR-0009 §6).

CREATE TABLE IF NOT EXISTS kge_graph_delta (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL DEFAULT (datetime('now', 'utc')),
    tier          TEXT    NOT NULL,         -- 'tier0' | 'tier2'
    op            TEXT    NOT NULL,         -- 'add' | 'remove'
    triple_count  INTEGER NOT NULL DEFAULT 0,
    entity_diff   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_kge_delta_ts
    ON kge_graph_delta(ts);

-- Tracks every triggered training cycle: score, tier decision, MRR delta.
CREATE TABLE IF NOT EXISTS kge_train_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT    NOT NULL,
    tier          TEXT    NOT NULL,         -- 'skip' | 'warm-start' | 'full' | 'hpo'
    change_score  REAL    NOT NULL DEFAULT 0.0,
    mrr_before    REAL,
    mrr_after     REAL,
    quality_gate  TEXT,                     -- 'passed' | 'failed' | NULL
    started_at    TEXT    NOT NULL DEFAULT (datetime('now', 'utc')),
    finished_at   TEXT,
    status        TEXT    NOT NULL DEFAULT 'running'  -- 'running'|'done'|'skipped'|'failed'
);

CREATE INDEX IF NOT EXISTS idx_kge_train_log_status
    ON kge_train_log(status, started_at);
