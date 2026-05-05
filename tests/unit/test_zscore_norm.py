"""
Z-score normalization tests for KGE surfacing pipeline (ADR-0009 §5).
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).parent.parent.parent
_MOD_PATH = _HERE / "app" / "services" / "kge" / "kge-surfacing.py"

spec = importlib.util.spec_from_file_location("kge_surfacing_z", _MOD_PATH)
_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["kge_surfacing_z"] = _mod
spec.loader.exec_module(_mod)  # type: ignore[union-attr]

zscore_normalize = _mod.zscore_normalize


def _pred(head: str, rel: str, tail: str, score: float) -> dict:
    return {
        "head_uri": head,
        "predicted_relation": rel,
        "tail_uri": tail,
        "score": score,
        "head_label": head.split(":")[-1],
        "tail_label": tail.split(":")[-1],
    }


# ── basic correctness ──────────────────────────────────────────────────────────

def test_zscore_single_group_mean_zero():
    preds = [
        _pred("urn:note:a", "rel:knows", "urn:note:b", 1.0),
        _pred("urn:note:a", "rel:knows", "urn:note:c", 3.0),
        _pred("urn:note:a", "rel:knows", "urn:note:d", 5.0),
    ]
    result = zscore_normalize(preds)
    zscores = [p["zscore"] for p in result]
    assert abs(sum(zscores)) < 1e-9, "z-scores in a group must sum to zero"


def test_zscore_single_group_unit_std():
    preds = [
        _pred("urn:note:a", "rel:knows", "urn:note:b", 1.0),
        _pred("urn:note:a", "rel:knows", "urn:note:c", 3.0),
        _pred("urn:note:a", "rel:knows", "urn:note:d", 5.0),
    ]
    result = zscore_normalize(preds)
    zscores = [p["zscore"] for p in result]
    variance = sum(z ** 2 for z in zscores) / len(zscores)
    assert abs(math.sqrt(variance) - 1.0) < 1e-6


def test_zscore_higher_score_higher_z():
    preds = [
        _pred("urn:note:a", "rel:r", "urn:note:b", 1.0),
        _pred("urn:note:a", "rel:r", "urn:note:c", 5.0),
    ]
    result = zscore_normalize(preds)
    z_by_tail = {p["tail_uri"]: p["zscore"] for p in result}
    assert z_by_tail["urn:note:c"] > z_by_tail["urn:note:b"]


def test_zscore_groups_are_independent():
    """Two different (head, relation) groups are normalized independently."""
    preds = [
        _pred("urn:note:a", "rel:r1", "urn:note:x", 10.0),
        _pred("urn:note:a", "rel:r1", "urn:note:y", 20.0),
        _pred("urn:note:b", "rel:r2", "urn:note:x", 0.1),
        _pred("urn:note:b", "rel:r2", "urn:note:y", 0.2),
    ]
    result = zscore_normalize(preds)
    group1 = [p["zscore"] for p in result if p["head_uri"] == "urn:note:a"]
    group2 = [p["zscore"] for p in result if p["head_uri"] == "urn:note:b"]
    # Both groups are mean-zero
    assert abs(sum(group1)) < 1e-9
    assert abs(sum(group2)) < 1e-9


def test_zscore_single_item_group_does_not_crash():
    """Single-item group has std=1 fallback — zscore = 0."""
    preds = [_pred("urn:note:a", "rel:r", "urn:note:b", 3.0)]
    result = zscore_normalize(preds)
    assert len(result) == 1
    assert result[0]["zscore"] == 0.0


def test_zscore_preserves_all_fields():
    pred = _pred("urn:note:a", "rel:r", "urn:note:b", 2.0)
    result = zscore_normalize([pred])
    assert result[0]["head_uri"] == pred["head_uri"]
    assert result[0]["score"] == pred["score"]
    assert "zscore" in result[0]


def test_zscore_empty_list():
    assert zscore_normalize([]) == []
