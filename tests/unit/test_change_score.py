"""
Tests for change_score computation and tier decision (phase-13, ADR-0009 §6).
"""
from __future__ import annotations

import importlib.util
import math
import sqlite3
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent.parent
_SCORE_PATH = _ROOT / "app" / "services" / "kge" / "change-score.py"

spec = importlib.util.spec_from_file_location("kge_change_score_test", _SCORE_PATH)
_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["kge_change_score_test"] = _mod
spec.loader.exec_module(_mod)  # type: ignore[union-attr]

compute_change_score = _mod.compute_change_score
decide_tier = _mod.decide_tier


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.execute("""
        CREATE TABLE kge_graph_delta (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            tier TEXT NOT NULL,
            op TEXT NOT NULL,
            triple_count INTEGER NOT NULL DEFAULT 0,
            entity_diff INTEGER NOT NULL DEFAULT 0
        )
    """)
    db.commit()
    return db


def _insert(conn, ts: str, tier: str, op: str, triples: int, entities: int = 0):
    conn.execute(
        "INSERT INTO kge_graph_delta (ts, tier, op, triple_count, entity_diff) VALUES (?,?,?,?,?)",
        (ts, tier, op, triples, entities),
    )
    conn.commit()


# ── decide_tier ────────────────────────────────────────────────────────────────

def test_decide_tier_skip():
    assert decide_tier(0.0) == "skip"
    assert decide_tier(49.9) == "skip"


def test_decide_tier_warm_start():
    assert decide_tier(50.0) == "warm-start"
    assert decide_tier(499.9) == "warm-start"


def test_decide_tier_full():
    assert decide_tier(500.0) == "full"
    assert decide_tier(1999.9) == "full"


def test_decide_tier_hpo():
    assert decide_tier(2000.0) == "hpo"
    assert decide_tier(9999.0) == "hpo"


# ── compute_change_score ───────────────────────────────────────────────────────

def test_zero_deltas_gives_only_time_component(conn):
    # No rows → only time contributes
    score = compute_change_score(conn, "2000-01-01T00:00:00Z", 10000, 1000, 30.0)
    # w_time * log(30+1) ≈ 10.0 * 3.434 ≈ 34.3
    assert score > 0
    assert score < 50  # below warm-start threshold


def test_large_delta_raises_score_above_skip(conn):
    _insert(conn, "2024-01-02T00:00:00Z", "tier0", "add", 500, 100)
    score = compute_change_score(conn, "2024-01-01T00:00:00Z", 10000, 1000, 1.0)
    assert score >= 50  # warm-start or higher


def test_remove_ops_do_not_contribute_positive(conn):
    _insert(conn, "2024-01-02T00:00:00Z", "tier0", "remove", 5000, 1000)
    score = compute_change_score(conn, "2024-01-01T00:00:00Z", 10000, 1000, 0.0)
    # Remove ops don't add to new_triples/new_entities; only time (0 days) contributes
    assert score < 50


def test_custom_weights_applied(conn):
    _insert(conn, "2024-01-02T00:00:00Z", "tier0", "add", 1000, 0)
    w_default = compute_change_score(conn, "2024-01-01T00:00:00Z", 10000, 1000, 1.0)
    w_boosted = compute_change_score(
        conn, "2024-01-01T00:00:00Z", 10000, 1000, 1.0,
        weights={"w_new_triples": 5.0, "w_new_entities": 1.5,
                 "w_predicate_diversity": 0.5, "w_time": 10.0},
    )
    assert w_boosted > w_default


def test_since_ts_filters_old_rows(conn):
    _insert(conn, "2024-01-01T00:00:00Z", "tier0", "add", 9999, 999)
    _insert(conn, "2024-01-03T00:00:00Z", "tier0", "add", 10, 1)
    # Only count rows on or after 2024-01-02
    score = compute_change_score(conn, "2024-01-02T00:00:00Z", 10000, 1000, 1.0)
    # Only 10 new triples (0.1% of total) → score should be much lower than 9999 case
    assert score < 100


def test_both_tiers_increase_diversity(conn):
    _insert(conn, "2024-01-02T00:00:00Z", "tier0", "add", 100, 10)
    score_one_tier = compute_change_score(conn, "2024-01-01T00:00:00Z", 10000, 1000, 0.0)

    _insert(conn, "2024-01-02T01:00:00Z", "tier2", "add", 100, 10)
    score_two_tiers = compute_change_score(conn, "2024-01-01T00:00:00Z", 10000, 1000, 0.0)

    assert score_two_tiers > score_one_tier  # more distinct tiers → higher diversity term
