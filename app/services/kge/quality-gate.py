"""
Quality gate + atomic model swap (ADR-0009 §6-7, phase-13 tasks 5-6).

new_MRR >= old_MRR * threshold → atomic swap (current → new_run_dir, old → history/<ts>/).
new_MRR <  old_MRR * threshold → keep_old, log quality_gate_failed.

Atomic swap: create tmp symlink → os.rename (POSIX-atomic) — never leaves current absent.
Keeps at most N=3 history generations; prunes oldest on each successful swap.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.logging import get_logger

log = get_logger(__name__)

_HISTORY_KEEP = 3
_MRR_THRESHOLD = 0.95


def run_quality_gate(
    new_metrics: dict,
    old_metrics: dict,
    new_run_dir: Path,
    kge_root: Path,
    threshold: float = _MRR_THRESHOLD,
) -> bool:
    """
    Check quality threshold. On pass: atomically swap current symlink.
    Returns True if gate passed (new model is now current).
    threshold defaults to 0.95 but is overridable from kge_config.yaml.
    """
    old_mrr = old_metrics.get("mrr", 0.0)
    new_mrr = new_metrics.get("mrr", 0.0)

    if old_mrr > 0 and new_mrr < old_mrr * threshold:
        log.warning("kge_quality_gate_failed", old_mrr=old_mrr, new_mrr=new_mrr,
                    threshold=threshold)
        return False

    _atomic_swap(new_run_dir, kge_root)
    log.info("kge_quality_gate_passed", old_mrr=old_mrr, new_mrr=new_mrr)
    return True


def read_current_metrics(kge_root: Path) -> dict:
    """Return metrics.json from current symlink, or zero dict if missing."""
    metrics_path = kge_root / "current" / "metrics.json"
    if metrics_path.exists():
        try:
            return json.loads(metrics_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"mrr": 0.0, "hits_at_10": 0.0}


# ── internal ───────────────────────────────────────────────────────────────────

def _atomic_swap(new_run_dir: Path, kge_root: Path) -> None:
    current_link = kge_root / "current"
    history_root = kge_root / "history"
    history_root.mkdir(parents=True, exist_ok=True)

    # Archive current before replacing it (while it still exists)
    if current_link.is_symlink():
        current_target = current_link.resolve()
        if current_target.exists() and current_target != new_run_dir.resolve():
            ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            archive_dest = history_root / ts
            shutil.copytree(str(current_target), str(archive_dest))
            log.info("kge_swap_archived", dest=str(archive_dest))
        _prune_history(history_root)

    # POSIX-atomic replace: create tmp symlink → os.rename atomically replaces current.
    # This guarantees `current` is never absent even if interrupted.
    tmp_link = kge_root / f".current.{os.getpid()}.tmp"
    tmp_link.symlink_to(new_run_dir, target_is_directory=True)
    os.rename(tmp_link, current_link)
    log.info("kge_swap", new_run=new_run_dir.name)


def _prune_history(history_root: Path) -> None:
    runs = sorted(
        (p for p in history_root.iterdir() if p.is_dir()),
        key=lambda p: p.name,
    )
    while len(runs) > _HISTORY_KEEP:
        oldest = runs.pop(0)
        shutil.rmtree(oldest, ignore_errors=True)
        log.info("kge_history_pruned", run=oldest.name)
