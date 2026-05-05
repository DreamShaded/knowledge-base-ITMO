"""
Tests for quality gate logic and rollback (phase-13, ADR-0009 §6-7).
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent.parent
_GATE_PATH = _ROOT / "app" / "services" / "kge" / "quality-gate.py"
_ROLLBACK_PATH = _ROOT / "app" / "services" / "kge" / "rollback.py"

spec_g = importlib.util.spec_from_file_location("kge_quality_gate_test", _GATE_PATH)
_gate = importlib.util.module_from_spec(spec_g)  # type: ignore[arg-type]
sys.modules["kge_quality_gate_test"] = _gate
spec_g.loader.exec_module(_gate)  # type: ignore[union-attr]

spec_r = importlib.util.spec_from_file_location("kge_rollback_test", _ROLLBACK_PATH)
_rollback = importlib.util.module_from_spec(spec_r)  # type: ignore[arg-type]
sys.modules["kge_rollback_test"] = _rollback
spec_r.loader.exec_module(_rollback)  # type: ignore[union-attr]

run_quality_gate = _gate.run_quality_gate
read_current_metrics = _gate.read_current_metrics
rollback_to = _rollback.rollback_to
list_history_runs = _rollback.list_history_runs
list_all_runs = _rollback.list_all_runs


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_run(parent: Path, name: str, mrr: float) -> Path:
    d = parent / name
    d.mkdir(parents=True)
    (d / "metrics.json").write_text(json.dumps({"mrr": mrr, "hits_at_10": 0.5}))
    (d / "model.pt").write_text("dummy")
    return d


# ── read_current_metrics ───────────────────────────────────────────────────────

def test_read_current_metrics_returns_zeros_when_missing(tmp_path):
    metrics = read_current_metrics(tmp_path)
    assert metrics["mrr"] == 0.0


def test_read_current_metrics_reads_symlink(tmp_path):
    run_dir = _make_run(tmp_path / "runs", "20240101T000000Z", 0.42)
    current = tmp_path / "current"
    current.symlink_to(run_dir, target_is_directory=True)
    metrics = read_current_metrics(tmp_path)
    assert metrics["mrr"] == pytest.approx(0.42)


# ── run_quality_gate ───────────────────────────────────────────────────────────

def test_gate_passes_when_no_old_model(tmp_path):
    new_run = _make_run(tmp_path / "runs", "20240101T000000Z", 0.40)
    passed = run_quality_gate({"mrr": 0.40}, {"mrr": 0.0}, new_run, tmp_path)
    assert passed is True
    assert (tmp_path / "current").is_symlink()


def test_gate_passes_when_new_mrr_above_threshold(tmp_path):
    old_run = _make_run(tmp_path / "runs", "20240101T000000Z", 0.40)
    (tmp_path / "current").symlink_to(old_run, target_is_directory=True)

    new_run = _make_run(tmp_path / "runs", "20240102T000000Z", 0.39)  # 97.5% of old
    passed = run_quality_gate({"mrr": 0.39}, {"mrr": 0.40}, new_run, tmp_path)
    assert passed is True


def test_gate_fails_when_new_mrr_below_threshold(tmp_path):
    new_run = _make_run(tmp_path / "runs", "20240102T000000Z", 0.30)
    passed = run_quality_gate({"mrr": 0.30}, {"mrr": 0.40}, new_run, tmp_path)
    # 0.30 < 0.40 * 0.95 = 0.38
    assert passed is False
    # current symlink should NOT point to new run
    assert not (tmp_path / "current").exists()


def test_gate_swap_archives_previous_run(tmp_path):
    old_run = _make_run(tmp_path / "runs", "20240101T000000Z", 0.40)
    (tmp_path / "current").symlink_to(old_run, target_is_directory=True)

    new_run = _make_run(tmp_path / "runs", "20240102T000000Z", 0.42)
    run_quality_gate({"mrr": 0.42}, {"mrr": 0.40}, new_run, tmp_path)

    history = list(sorted((tmp_path / "history").iterdir()))
    assert len(history) == 1
    assert (history[0] / "metrics.json").exists()


def test_gate_prunes_history_beyond_3(tmp_path):
    # Seed 3 history entries manually
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    for i in range(3):
        _make_run(history_dir, f"2024010{i + 1}T000000Z", 0.30 + i * 0.01)

    old_run = _make_run(tmp_path / "runs", "20240104T000000Z", 0.40)
    (tmp_path / "current").symlink_to(old_run, target_is_directory=True)

    new_run = _make_run(tmp_path / "runs", "20240105T000000Z", 0.45)
    run_quality_gate({"mrr": 0.45}, {"mrr": 0.40}, new_run, tmp_path)

    remaining = list(history_dir.iterdir())
    # After archiving old_run (4th) and pruning oldest, we should have ≤3
    assert len(remaining) <= 3


# ── rollback_to ────────────────────────────────────────────────────────────────

def test_rollback_swaps_symlink(tmp_path):
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    old_run = _make_run(history_dir, "20240101T000000Z", 0.38)

    new_run = _make_run(tmp_path / "runs", "20240102T000000Z", 0.45)
    (tmp_path / "current").symlink_to(new_run, target_is_directory=True)

    rollback_to("20240101T000000Z", tmp_path)

    resolved = (tmp_path / "current").resolve()
    assert resolved == old_run.resolve()


def test_rollback_raises_for_unknown_run(tmp_path):
    with pytest.raises(FileNotFoundError):
        rollback_to("nonexistent-run", tmp_path)


def test_list_history_runs_empty(tmp_path):
    assert list_history_runs(tmp_path) == []


def test_list_history_runs_returns_sorted_desc(tmp_path):
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    _make_run(history_dir, "20240101T000000Z", 0.30)
    _make_run(history_dir, "20240103T000000Z", 0.35)

    runs = list_history_runs(tmp_path)
    assert runs[0]["run_id"] == "20240103T000000Z"
    assert runs[1]["run_id"] == "20240101T000000Z"
