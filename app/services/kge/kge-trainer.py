"""
RotatE trainer using PyKEEN (ADR-0009 §1-3).
Lazy import of pykeen — avoids startup cost; import only when training runs.

Artifacts saved to data/kge/runs/<ts>/ per ADR-0009 §4.
"""
from __future__ import annotations

import json
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.logging import get_logger

log = get_logger(__name__)

_KGE_RUNS_BASE = Path("data") / "kge" / "runs"


def build_triples_factory(triples: list[tuple[str, str, str]]):
    """Build PyKEEN TriplesFactory split 80/10/10 with fixed random state."""
    import numpy as np
    from pykeen.triples import TriplesFactory

    arr = np.array(triples, dtype=str)
    factory = TriplesFactory.from_labeled_triples(arr, create_inverse_triples=False)
    training, valid, test = factory.split([0.8, 0.1, 0.1], random_state=42)
    return training, valid, test, factory


def train_rotate(
    training,
    valid,
    testing=None,
    *,
    embedding_dim: int = 256,
    epochs: int = 200,
    batch_size: int = 512,
    neg_per_pos: int = 64,
    lr: float = 1e-3,
    patience: int = 20,
    device: str = "cpu",
) -> tuple:
    """Train RotatE; return (model, metrics_dict)."""
    from pykeen.pipeline import pipeline

    result = pipeline(
        training=training,
        testing=testing if testing is not None else valid,
        validation=valid,
        model="RotatE",
        model_kwargs={"embedding_dim": embedding_dim},
        loss="NSSALoss",
        loss_kwargs={"margin": 12.0, "adversarial_temperature": 1.0},
        optimizer="Adam",
        optimizer_kwargs={"lr": lr},
        training_loop="sLCWA",
        training_kwargs={
            "num_epochs": epochs,
            "batch_size": batch_size,
            "use_tqdm": False,
        },
        negative_sampler_kwargs={"num_negs_per_pos": neg_per_pos},
        stopper="early",
        stopper_kwargs={"patience": patience, "metric": "mrr", "relative_delta": 0.001},
        evaluator_kwargs={"filtered": True},
        device=device,
        random_seed=42,
    )
    metrics = _extract_metrics(result)
    log.info("kge_training_done", **metrics)
    return result.model, metrics


def _extract_metrics(result) -> dict:
    try:
        flat = result.metric_results.to_flat_dict()
        mrr = float(flat.get("both.realistic.inverse_harmonic_mean_rank", 0.0))
        h10 = float(flat.get("both.realistic.hits_at_10", 0.0))
    except Exception:
        try:
            mrr = float(result.get_metric("mean_reciprocal_rank") or 0.0)
            h10 = float(result.get_metric("hits_at_10") or 0.0)
        except Exception:
            mrr, h10 = 0.0, 0.0
    return {"mrr": mrr, "hits_at_10": h10}


def make_run_dir(repo_root: Path) -> tuple[Path, str]:
    """Return (run_dir, run_id) using current timestamp."""
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = repo_root / _KGE_RUNS_BASE / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir, ts


def save_run(
    run_dir: Path,
    model,
    factory,
    metrics: dict,
    config: dict,
) -> None:
    """Persist all run artifacts (ADR-0009 §4)."""
    import torch

    torch.save(model, run_dir / "model.pt")
    with open(run_dir / "triples_factory.pkl", "wb") as f:
        pickle.dump(factory, f)
    (run_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    (run_dir / "config.yaml").write_text(
        yaml.dump(config, default_flow_style=False, allow_unicode=True), encoding="utf-8"
    )
    (run_dir / "training.log").write_text(
        f"run_id: {run_dir.name}\n"
        + "\n".join(f"{k}: {v}" for k, v in metrics.items()),
        encoding="utf-8",
    )
    _update_current_symlink(run_dir)
    log.info("kge_run_saved", run_dir=str(run_dir), **metrics)


def _update_current_symlink(run_dir: Path) -> None:
    symlink = run_dir.parent.parent / "current"
    if symlink.is_symlink() or symlink.exists():
        symlink.unlink()
    symlink.symlink_to(run_dir, target_is_directory=True)


def load_run(run_dir: Path) -> tuple:
    """Load (model, factory) from a saved run directory."""
    import torch

    model = torch.load(run_dir / "model.pt", map_location="cpu", weights_only=False)
    with open(run_dir / "triples_factory.pkl", "rb") as f:
        factory = pickle.load(f)
    return model, factory


def check_quality_gate(new_metrics: dict, old_metrics: dict) -> bool:
    """Return True if new model passes quality gate (ADR-0009 §6, used in phase-13).
    False → keep_old; log quality_gate_failed."""
    old_mrr = old_metrics.get("mrr", 0.0)
    new_mrr = new_metrics.get("mrr", 0.0)
    if old_mrr > 0 and new_mrr < old_mrr * 0.95:
        log.warning("quality_gate_failed", old_mrr=old_mrr, new_mrr=new_mrr)
        return False
    return True
