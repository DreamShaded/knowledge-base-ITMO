"""
Adaptive retrain scheduler for KGE (ADR-0009 §6, phase-13 tasks 3-4).
Entry point: run_nightly_cycle(conn, gpu_mutex, repo_root, config).

Tier decisions:
  change_score < 50    → skip
  50–500               → warm-start (20–30 epochs incremental)
  500–2000             → full retrain (200 epochs)
  > 2000               → full + HPO (grid search)
Weekly hedge: Sunday forces full regardless of score.
Event trigger: >100 new tier0 triples in 24h escalates skip → warm-start.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from app.logging import get_logger

log = get_logger(__name__)

_GPU_DEFER_MAX = 3
_GPU_DEFER_SECS = 3600  # 1-hour deferral


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def run_nightly_cycle(conn, gpu_mutex, repo_root: Path, config: dict) -> str:
    """Orchestrate the full nightly retrain cycle. Returns tier decision string."""
    base = repo_root / "app" / "services" / "kge"
    score_mod = _load("kge_change_score", base / "change-score.py")
    delta_mod = _load("kge_delta_tracker", base / "delta-tracker.py")
    gate_mod = _load("kge_quality_gate", base / "quality-gate.py")

    last_ts = _last_train_ts(conn)
    days = _days_since(last_ts)
    total_t, total_e = _total_counts(conn)
    weights = config.get("change_score", {}).get("weights")

    score = score_mod.compute_change_score(conn, last_ts, total_t, total_e, days, weights)
    tier = score_mod.decide_tier(score)

    # Weekly Sunday hedge
    if _is_sunday() and tier in ("skip", "warm-start"):
        log.info("kge_weekly_hedge", from_tier=tier)
        tier = "full"

    # Event-based trigger: >100 new notes in 24h
    if tier == "skip" and delta_mod.count_new_notes_last_24h(conn) > 100:
        log.info("kge_event_trigger_100_notes")
        tier = "warm-start"

    log_id = _log_start(conn, score, tier)

    if tier == "skip":
        log.info("kge_retrain_skipped", change_score=score)
        _log_done(conn, log_id, "skipped")
        return tier

    _wait_for_gpu(gpu_mutex, tier)
    _run_train(tier, repo_root, config, conn, gate_mod, log_id)
    return tier


# ── training execution ─────────────────────────────────────────────────────────

def _run_train(tier: str, repo_root: Path, config: dict, conn, gate_mod, log_id: int) -> None:
    base = repo_root / "app" / "services" / "kge"
    trainer = _load("kge_trainer_sched", base / "kge-trainer.py")
    loader = _load("kge_triples_loader_sched", base / "triples-loader.py")
    kge_root = repo_root / "data" / "kge"
    old_metrics = gate_mod.read_current_metrics(kge_root)

    try:
        from app.stores.oxigraph import OxigraphStore
        ox = OxigraphStore(path=str(repo_root / "data" / "oxigraph"))
        triples = loader.load_training_triples(ox)
    except Exception as exc:
        log.error("kge_load_triples_failed", error=str(exc))
        _log_done(conn, "failed")
        return

    if not triples:
        log.warning("kge_no_triples")
        _log_done(conn, "failed")
        return

    kge_cfg = config.get("kge", {})
    epochs = _epochs_for_tier(tier, kge_cfg)

    try:
        training, valid, _test, factory = trainer.build_triples_factory(triples)
        model, metrics = trainer.train_rotate(training, valid, epochs=epochs)
        run_dir, _ = trainer.make_run_dir(repo_root)
        trainer.save_run(run_dir, model, factory, metrics, kge_cfg)

        passed = gate_mod.run_quality_gate(metrics, old_metrics, run_dir, kge_root)
        _log_quality(conn, log_id, old_metrics.get("mrr", 0.0), metrics.get("mrr", 0.0),
                     "passed" if passed else "failed")
        _log_done(conn, log_id, "done")
    except Exception as exc:
        log.error("kge_train_failed", error=str(exc))
        _log_done(conn, log_id, "failed")


def _epochs_for_tier(tier: str, kge_cfg: dict) -> int:
    base = kge_cfg.get("epochs", 200)
    if "warm" in tier:
        return min(30, max(20, base // 8))
    return base


# ── GPU scheduling ─────────────────────────────────────────────────────────────

def _wait_for_gpu(gpu_mutex, tier: str) -> None:
    if gpu_mutex is None:
        return
    for attempt in range(1, _GPU_DEFER_MAX + 1):
        try:
            depth = gpu_mutex.queue_depth()
        except Exception:
            depth = 0
        if depth == 0:
            return
        log.info("kge_gpu_defer", attempt=attempt, tier=tier)
        time.sleep(_GPU_DEFER_SECS)
    log.warning("kge_gpu_defer_max_reached", tier=tier)


# ── helpers ────────────────────────────────────────────────────────────────────

def _last_train_ts(conn) -> str:
    row = conn.execute(
        "SELECT started_at FROM kge_train_log WHERE status='done' "
        "ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else "2000-01-01T00:00:00Z"


def _days_since(ts: str) -> float:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return (datetime.now(tz=timezone.utc) - dt).total_seconds() / 86400


def _is_sunday() -> bool:
    return datetime.now(tz=timezone.utc).weekday() == 6


def _total_counts(conn) -> tuple[int, int]:
    t = conn.execute(
        "SELECT COALESCE(SUM(triple_count), 10000) FROM kge_graph_delta WHERE op='add'"
    ).fetchone()
    e = conn.execute(
        "SELECT COALESCE(SUM(entity_diff), 1000) FROM kge_graph_delta WHERE op='add'"
    ).fetchone()
    return int(t[0]) if t else 10000, int(e[0]) if e else 1000


def _log_start(conn, score: float, tier: str) -> int:
    """Insert train log row; return lastrowid for subsequent scoped updates."""
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cur = conn.execute(
        "INSERT INTO kge_train_log (run_id, tier, change_score, started_at, status) "
        "VALUES (?,?,?,?,?)",
        (ts, tier, score, ts, "running"),
    )
    conn.commit()
    return cur.lastrowid


def _log_done(conn, log_id: int, status: str) -> None:
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "UPDATE kge_train_log SET finished_at=?, status=? WHERE id=?",
        (ts, status, log_id),
    )
    conn.commit()


def _log_quality(conn, log_id: int, mrr_before: float, mrr_after: float, gate: str) -> None:
    conn.execute(
        "UPDATE kge_train_log SET mrr_before=?, mrr_after=?, quality_gate=? WHERE id=?",
        (mrr_before, mrr_after, gate, log_id),
    )
    conn.commit()
