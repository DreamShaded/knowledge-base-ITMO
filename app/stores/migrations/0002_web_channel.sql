-- Phase-07: web channel tables.
-- web_search_cache: SearXNG search result cache (24h default, 1h fresh-mode).
-- web_page_cache: extracted page content cache (7d).
-- daily_quota: per-UTC-day web request counter.
-- web_saved_index: tracks URL → content_hash mapping for /save-web idempotency.

CREATE TABLE IF NOT EXISTS web_search_cache (
    idempotency_key  TEXT PRIMARY KEY,     -- sha256(query + sorted_engines)
    query            TEXT NOT NULL,
    results_json     TEXT NOT NULL,        -- JSON array of WebSearchResult
    fetched_at       TEXT NOT NULL,
    expires_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS web_page_cache (
    url_hash          TEXT PRIMARY KEY,    -- sha256(url)
    url               TEXT NOT NULL,
    content_md        TEXT NOT NULL,
    title             TEXT NOT NULL DEFAULT '',
    extraction_method TEXT NOT NULL,       -- "trafilatura" | "playwright" | "snippet"
    content_hash      TEXT NOT NULL,       -- sha256(content_md)
    fetched_at        TEXT NOT NULL,
    expires_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_quota (
    date             TEXT PRIMARY KEY,     -- UTC date YYYY-MM-DD
    count            INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS web_saved_index (
    url                   TEXT PRIMARY KEY,
    content_hash          TEXT NOT NULL,
    fetched_at            TEXT NOT NULL,
    previous_hashes_json  TEXT NOT NULL DEFAULT '[]'
);
