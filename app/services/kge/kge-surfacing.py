"""
Multi-step KGE prediction surfacing pipeline (ADR-0009 §5).

Steps:
  1. Z-score normalization per (entity, relation) group
  2. Adaptive per-entity quota based on top z-score → 1-7 predictions
  3. Round-robin diversity by entity_type
  4. Skip already-reviewed (approved/rejected/deferred within 7 days)
  5. Re-show if score grew ≥ 0.30 since last surfacing
  6. Global cap: max 50 surfaced per week
Output → kge_review_queue SQLite table.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterator

from app.logging import get_logger

log = get_logger(__name__)

_WEEKLY_CAP = 50
_RESCORE_THRESHOLD = 0.30

# z-score → max predictions per entity
_QUOTA_LADDER: list[tuple[float, int]] = [
    (3.0, 7),
    (2.0, 5),
    (1.0, 3),
    (0.0, 1),
]


def zscore_normalize(predictions: list[dict]) -> list[dict]:
    """Add 'zscore' field: normalized per (entity, relation) group."""
    from collections import defaultdict
    import math

    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for p in predictions:
        key = (p["head_uri"], p["predicted_relation"])
        groups[key].append(p["score"])

    stats: dict[tuple[str, str], tuple[float, float]] = {}
    for key, scores in groups.items():
        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        std = math.sqrt(variance) if variance > 0 else 1.0
        stats[key] = (mean, std)

    result = []
    for p in predictions:
        key = (p["head_uri"], p["predicted_relation"])
        mean, std = stats[key]
        z = (p["score"] - mean) / std
        result.append({**p, "zscore": z})
    return result


def adaptive_quota(entity_preds: list[dict]) -> int:
    """Return quota for an entity based on its top z-score."""
    if not entity_preds:
        return 0
    top_z = max(p["zscore"] for p in entity_preds)
    for threshold, quota in _QUOTA_LADDER:
        if top_z >= threshold:
            return quota
    return 1


def infer_entity_type(uri: str) -> str:
    if "urn:note:" in uri:
        return "note"
    if "urn:tag:" in uri:
        return "tag"
    if "urn:wd:" in uri or "wikidata.org" in uri:
        return "wikidata"
    return "concept"


def surface_predictions(
    predictions: list[dict],
    sqlite_conn: sqlite3.Connection,
    run_id: str,
) -> int:
    """
    Run full surfacing pipeline. Returns count of new items added to queue.
    """
    if not predictions:
        return 0

    scored = zscore_normalize(predictions)
    now = datetime.now(tz=timezone.utc)
    week_start = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Count already-surfaced this week
    row = sqlite_conn.execute(
        "SELECT COUNT(*) FROM kge_review_queue WHERE surfaced_at >= ?", (week_start,)
    ).fetchone()
    already_surfaced = row[0] if row else 0
    remaining_cap = max(0, _WEEKLY_CAP - already_surfaced)
    if remaining_cap == 0:
        log.info("kge_surfacing_weekly_cap_reached")
        return 0

    # Load recently-reviewed to skip
    recently_reviewed = _load_recently_reviewed(sqlite_conn, now)
    # Load previous scores to detect re-show candidates
    prev_scores = _load_previous_scores(sqlite_conn)

    # Group by entity
    by_entity: dict[str, list[dict]] = defaultdict(list)
    for p in scored:
        by_entity[p["head_uri"]].append(p)

    # Sort each entity's predictions by zscore desc
    for preds in by_entity.values():
        preds.sort(key=lambda x: x["zscore"], reverse=True)

    # Build per-entity candidates with quota
    type_buckets: dict[str, list[dict]] = defaultdict(list)
    for entity_uri, preds in by_entity.items():
        quota = adaptive_quota(preds)
        entity_type = infer_entity_type(entity_uri)
        count = 0
        for p in preds:
            if count >= quota:
                break
            key = (p["head_uri"], p["predicted_relation"], p["tail_uri"])
            if key in recently_reviewed:
                # Re-show only if score grew significantly
                prev = prev_scores.get(key, p["score"])
                if p["score"] - prev < _RESCORE_THRESHOLD:
                    continue
            p["entity_type"] = entity_type
            p["justification"] = _build_justification(p)
            type_buckets[entity_type].append(p)
            count += 1

    # Round-robin by entity_type for diversity
    surfaced = list(_round_robin(type_buckets.values(), remaining_cap))

    _insert_queue(sqlite_conn, surfaced, run_id, now)
    log.info("kge_surfacing_done", surfaced=len(surfaced))
    return len(surfaced)


def _load_recently_reviewed(
    conn: sqlite3.Connection, now: datetime
) -> set[tuple[str, str, str]]:
    cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        "SELECT head_uri, predicted_rel, tail_uri FROM kge_review_queue "
        "WHERE status IN ('approved','rejected') OR "
        "(status='deferred' AND defer_until >= ?)",
        (cutoff,),
    ).fetchall()
    return {(r[0], r[1], r[2]) for r in rows}


def _load_previous_scores(conn: sqlite3.Connection) -> dict[tuple, float]:
    rows = conn.execute(
        "SELECT head_uri, predicted_rel, tail_uri, score FROM kge_review_queue"
    ).fetchall()
    return {(r[0], r[1], r[2]): r[3] for r in rows}


def _build_justification(pred: dict) -> str:
    h = pred.get("head_label") or pred["head_uri"].split("/")[-1]
    t = pred.get("tail_label") or pred["tail_uri"].split("/")[-1]
    return f"predicted link between {h} and {t}"


def _round_robin(buckets, cap: int) -> Iterator[dict]:
    queues = [list(b) for b in buckets if b]
    emitted = 0
    while queues and emitted < cap:
        for q in list(queues):
            if emitted >= cap:
                break
            if q:
                yield q.pop(0)
                emitted += 1
        queues = [q for q in queues if q]


def _insert_queue(
    conn: sqlite3.Connection,
    items: list[dict],
    run_id: str,
    now: datetime,
) -> None:
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.executemany(
        """INSERT INTO kge_review_queue
           (run_id, head_uri, predicted_rel, tail_uri, score, zscore,
            head_label, tail_label, justification, entity_type, surfaced_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                run_id,
                p["head_uri"], p["predicted_relation"], p["tail_uri"],
                p["score"], p.get("zscore", 0.0),
                p.get("head_label", ""), p.get("tail_label", ""),
                p.get("justification", ""), p.get("entity_type", ""),
                ts,
            )
            for p in items
        ],
    )
    conn.commit()
