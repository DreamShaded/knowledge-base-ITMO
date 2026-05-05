"""
KGE 3-generation rollback (ADR-0009, phase-13 task 6).
`rollback_to(run_id, kge_root)` atomically swaps current symlink to a history run.
`list_all_runs(kge_root)` shows runs/ + marks which is current.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.logging import get_logger

log = get_logger(__name__)


def list_history_runs(kge_root: Path) -> list[dict]:
    """List runs preserved in data/kge/history/ (up to 3 generations)."""
    history_root = kge_root / "history"
    if not history_root.exists():
        return []
    return [
        {"run_id": d.name, "mrr": _read_mrr(d / "metrics.json"), "path": str(d)}
        for d in sorted(history_root.iterdir(), reverse=True)
        if d.is_dir()
    ]


def list_all_runs(kge_root: Path) -> list[dict]:
    """List all runs in data/kge/runs/, marking the current one."""
    runs_root = kge_root / "runs"
    if not runs_root.exists():
        return []
    current_target = _resolve_current(kge_root)
    runs = []
    for d in sorted(runs_root.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        runs.append({
            "run_id": d.name,
            "mrr": _read_mrr(d / "metrics.json"),
            "current": d.resolve() == current_target,
            "path": str(d),
        })
    return runs


def rollback_to(run_id: str, kge_root: Path) -> Path:
    """
    Swap current symlink to a specific run_id.
    Searches history/ first, then runs/.
    Raises FileNotFoundError if run_id not found.
    """
    for search_root in (kge_root / "history", kge_root / "runs"):
        target = search_root / run_id
        if target.exists():
            break
    else:
        raise FileNotFoundError(f"KGE run not found: {run_id}")

    current_link = kge_root / "current"
    if current_link.is_symlink():
        current_link.unlink()
    current_link.symlink_to(target, target_is_directory=True)
    log.info("kge_rollback", run_id=run_id, target=str(target))
    return target


# ── internal ───────────────────────────────────────────────────────────────────

def _resolve_current(kge_root: Path) -> Path | None:
    link = kge_root / "current"
    if link.is_symlink():
        return link.resolve()
    return None


def _read_mrr(metrics_path: Path) -> float:
    if not metrics_path.exists():
        return 0.0
    try:
        return json.loads(metrics_path.read_text(encoding="utf-8")).get("mrr", 0.0)
    except Exception:
        return 0.0
