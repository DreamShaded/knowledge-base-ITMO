"""
GraphDelta tracker — records triple add/remove events from Tier-0 and Wikidata writers.
Hooks called after batch writes; rows feed change_score computation (phase-13, ADR-0009 §6).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from app.logging import get_logger

log = get_logger(__name__)


def record_delta(
    conn: sqlite3.Connection,
    tier: str,           # 'tier0' | 'tier2'
    op: str,             # 'add' | 'remove'
    triple_count: int,
    entity_diff: int = 0,
) -> None:
    """Record a batch of triple changes to kge_graph_delta."""
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO kge_graph_delta (ts, tier, op, triple_count, entity_diff) VALUES (?,?,?,?,?)",
        (ts, tier, op, triple_count, entity_diff),
    )
    conn.commit()
    log.info("kge_delta_recorded", tier=tier, op=op, triple_count=triple_count)


def get_deltas_since(conn: sqlite3.Connection, since_ts: str) -> list[dict]:
    """Return all delta rows since since_ts (ISO-8601 UTC string), ordered by ts."""
    rows = conn.execute(
        "SELECT ts, tier, op, triple_count, entity_diff FROM kge_graph_delta "
        "WHERE ts >= ? ORDER BY ts",
        (since_ts,),
    ).fetchall()
    return [dict(r) for r in rows]


def count_new_notes_last_24h(conn: sqlite3.Connection) -> int:
    """Return total tier0-add triple_count in the last 24 hours (event-based trigger)."""
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = conn.execute(
        "SELECT COALESCE(SUM(triple_count), 0) FROM kge_graph_delta "
        "WHERE tier='tier0' AND op='add' AND ts >= ?",
        (cutoff,),
    ).fetchone()
    return int(row[0]) if row else 0
