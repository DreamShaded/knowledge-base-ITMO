"""
change_score computation (ADR-0009 §6, phase-13 task 2).

score = w_new_triples × (new/total) × 1000
       + w_new_entities × (new_E/total_E) × 500
       + w_predicate_diversity × log(distinct_tiers + 1) × 100
       + w_time × log(days_since_last + 1)

Tier thresholds:  <50 → skip | 50-500 → warm-start | 500-2000 → full | >2000 → hpo
"""
from __future__ import annotations

import math
import sqlite3

from app.logging import get_logger

log = get_logger(__name__)

_DEFAULT_WEIGHTS = {
    "w_new_triples": 1.0,
    "w_new_entities": 1.5,
    "w_predicate_diversity": 0.5,
    "w_time": 10.0,
}


def compute_change_score(
    conn: sqlite3.Connection,
    since_ts: str,
    total_triples: int,
    total_entities: int,
    days_since_last: float,
    weights: dict | None = None,
) -> float:
    """Compute change_score from kge_graph_delta rows since since_ts."""
    w = {**_DEFAULT_WEIGHTS, **(weights or {})}

    rows = conn.execute(
        "SELECT op, SUM(triple_count), SUM(entity_diff) FROM kge_graph_delta "
        "WHERE ts >= ? GROUP BY op",
        (since_ts,),
    ).fetchall()

    new_triples = 0
    new_entities = 0
    for row in rows:
        if row[0] == "add":
            new_triples += int(row[1] or 0)
            new_entities += int(row[2] or 0)

    tier_row = conn.execute(
        "SELECT COUNT(DISTINCT tier) FROM kge_graph_delta WHERE op='add' AND ts >= ?",
        (since_ts,),
    ).fetchone()
    distinct_tiers = tier_row[0] if tier_row else 0

    t_ratio = new_triples / max(total_triples, 1)
    e_ratio = new_entities / max(total_entities, 1)

    score = (
        w["w_new_triples"] * t_ratio * 1000
        + w["w_new_entities"] * e_ratio * 500
        + w["w_predicate_diversity"] * math.log(distinct_tiers + 1) * 100
        + w["w_time"] * math.log(days_since_last + 1)
    )

    log.info(
        "kge_change_score",
        score=round(score, 2),
        new_triples=new_triples,
        new_entities=new_entities,
        days_since_last=round(days_since_last, 1),
    )
    return score


def decide_tier(score: float) -> str:
    """Map change_score to retrain tier (ADR-0009 §6)."""
    if score < 50:
        return "skip"
    if score < 500:
        return "warm-start"
    if score < 2000:
        return "full"
    return "hpo"
