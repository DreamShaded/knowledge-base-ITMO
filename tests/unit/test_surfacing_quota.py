"""
Multi-step surfacing quota logic tests (ADR-0009 §5).
Tests adaptive_quota, _round_robin, and surface_predictions weekly cap.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).parent.parent.parent
_MOD_PATH = _HERE / "app" / "services" / "kge" / "kge-surfacing.py"

spec = importlib.util.spec_from_file_location("kge_surfacing", _MOD_PATH)
_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["kge_surfacing"] = _mod
spec.loader.exec_module(_mod)  # type: ignore[union-attr]

adaptive_quota = _mod.adaptive_quota
zscore_normalize = _mod.zscore_normalize
surface_predictions = _mod.surface_predictions
infer_entity_type = _mod.infer_entity_type
_WEEKLY_CAP = _mod._WEEKLY_CAP


# ── adaptive_quota ─────────────────────────────────────────────────────────────

def _pred(zscore: float) -> dict:
    return {"head_uri": "urn:note:a", "predicted_relation": "p", "tail_uri": "urn:note:b",
            "score": 1.0, "zscore": zscore}


def test_quota_high_zscore_returns_7():
    assert adaptive_quota([_pred(3.5)]) == 7


def test_quota_mid_zscore_returns_5():
    assert adaptive_quota([_pred(2.5)]) == 5


def test_quota_low_zscore_returns_3():
    assert adaptive_quota([_pred(1.5)]) == 3


def test_quota_minimal_zscore_returns_1():
    assert adaptive_quota([_pred(0.1)]) == 1


def test_quota_empty_list_returns_0():
    assert adaptive_quota([]) == 0


def test_quota_uses_max_zscore_from_list():
    # Multiple preds — quota based on top, not average
    preds = [_pred(0.5), _pred(2.1), _pred(1.0)]
    assert adaptive_quota(preds) == 5


# ── infer_entity_type ──────────────────────────────────────────────────────────

def test_infer_note_type():
    assert infer_entity_type("urn:note:v:reactivity.md") == "note"


def test_infer_tag_type():
    assert infer_entity_type("urn:tag:machine-learning") == "tag"


def test_infer_wikidata_type():
    assert infer_entity_type("urn:wd:Q5") == "wikidata"


def test_infer_concept_type_fallback():
    assert infer_entity_type("urn:concept:reactivity") == "concept"


# ── weekly cap enforcement ─────────────────────────────────────────────────────

def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE kge_review_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            head_uri TEXT NOT NULL, predicted_rel TEXT NOT NULL, tail_uri TEXT NOT NULL,
            score REAL NOT NULL, zscore REAL NOT NULL DEFAULT 0.0,
            head_label TEXT NOT NULL DEFAULT '', tail_label TEXT NOT NULL DEFAULT '',
            justification TEXT NOT NULL DEFAULT '', entity_type TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            defer_until TEXT, reject_reason TEXT, reviewed_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            surfaced_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)
    return conn


def _make_preds(n: int) -> list[dict]:
    return [
        {
            "head_uri": f"urn:note:n{i}", "predicted_relation": "p",
            "tail_uri": f"urn:note:t{i}", "score": 1.0 + i * 0.1,
            "head_label": f"n{i}", "tail_label": f"t{i}",
        }
        for i in range(n)
    ]


def test_surface_predictions_inserts_up_to_cap():
    conn = _make_conn()
    preds = _make_preds(60)  # more than weekly cap
    inserted = surface_predictions(preds, conn, "run-001")
    assert inserted <= _WEEKLY_CAP


def test_surface_predictions_respects_existing_weekly_surfaced():
    conn = _make_conn()
    # Pre-fill 48 rows surfaced this week
    conn.executemany(
        "INSERT INTO kge_review_queue (run_id, head_uri, predicted_rel, tail_uri, score) "
        "VALUES (?,?,?,?,?)",
        [("r0", f"urn:note:x{i}", "p", f"urn:note:y{i}", 1.0) for i in range(48)],
    )
    conn.commit()

    preds = _make_preds(10)
    inserted = surface_predictions(preds, conn, "run-001")
    assert inserted <= 2  # cap=50, 48 already used → max 2 remaining


def test_surface_predictions_weekly_cap_full_returns_zero():
    conn = _make_conn()
    conn.executemany(
        "INSERT INTO kge_review_queue (run_id, head_uri, predicted_rel, tail_uri, score) "
        "VALUES (?,?,?,?,?)",
        [("r0", f"urn:note:x{i}", "p", f"urn:note:y{i}", 1.0) for i in range(50)],
    )
    conn.commit()

    inserted = surface_predictions(_make_preds(5), conn, "run-001")
    assert inserted == 0
