-- Phase: semantic link discovery — per-vault note similarity candidates.
-- Each row is an unordered pair (source, target) within the same vault.

CREATE TABLE IF NOT EXISTS semantic_link_candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    vault_id        TEXT    NOT NULL,
    source_note_id  TEXT    NOT NULL,   -- "vault_id:relative/path.md"
    target_note_id  TEXT    NOT NULL,   -- "vault_id:relative/path.md"
    score           REAL    NOT NULL,   -- cosine similarity [0, 1]
    status          TEXT    NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|deferred
    defer_until     TEXT,               -- ISO-8601 UTC; set on defer
    reviewed_at     TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_slc_status
    ON semantic_link_candidates(status, defer_until);

CREATE INDEX IF NOT EXISTS idx_slc_vault
    ON semantic_link_candidates(vault_id, status);

-- Prevent duplicate pairs regardless of direction
CREATE UNIQUE INDEX IF NOT EXISTS idx_slc_pair
    ON semantic_link_candidates(
        min(source_note_id, target_note_id),
        max(source_note_id, target_note_id)
    );
