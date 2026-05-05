-- Wikidata enrichment cache (phase-11).
-- TTL split: L1/L2 = 30 days, L3 = 7 days (stored in expires_at).
-- Primary key (qid, level) allows cheap upserts.

CREATE TABLE IF NOT EXISTS wikidata_cache (
    qid        TEXT NOT NULL,
    level      TEXT NOT NULL CHECK(level IN ('L1', 'L2', 'L3')),
    payload    TEXT NOT NULL,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    PRIMARY KEY (qid, level)
);

CREATE INDEX IF NOT EXISTS idx_wikidata_cache_expires
    ON wikidata_cache(expires_at);
