"""
FB15k-237 sanity check for RotatE pipeline (ADR-0009 §3, PRD §11 p.4).

Trains RotatE 256-dim for 100 epochs on FB15k-237 and validates that the
pipeline is wired correctly before running on personal graph.

Acceptance thresholds: MRR > 0.30, Hits@10 > 0.50.
Result saved to data/kge/sanity-check/fb15k_237.json.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.logging import get_logger

log = get_logger(__name__)

_MRR_THRESHOLD = 0.30
_HITS10_THRESHOLD = 0.50
_SANITY_EPOCHS = 100
_SANITY_EMBEDDING_DIM = 256


def run_sanity_check(repo_root: Path, device: str = "cpu") -> dict:
    """
    Train RotatE on FB15k-237 and validate metrics meet acceptance thresholds.
    Returns result dict with 'passed', 'mrr', 'hits_at_10'.
    """
    from pykeen.datasets import FB15k237
    from pykeen.pipeline import pipeline

    log.info("kge_sanity_check_start", dataset="FB15k-237", epochs=_SANITY_EPOCHS)

    dataset = FB15k237()

    result = pipeline(
        training=dataset.training,
        validation=dataset.validation,
        testing=dataset.testing,
        model="RotatE",
        model_kwargs={"embedding_dim": _SANITY_EMBEDDING_DIM},
        loss="NSSALoss",
        loss_kwargs={"margin": 12.0, "adversarial_temperature": 1.0},
        optimizer="Adam",
        optimizer_kwargs={"lr": 1e-3},
        training_loop="sLCWA",
        training_kwargs={
            "num_epochs": _SANITY_EPOCHS,
            "batch_size": 512,
            "use_tqdm": False,
        },
        negative_sampler_kwargs={"num_negs_per_pos": 64},
        evaluator_kwargs={"filtered": True},
        device=device,
        random_seed=42,
    )

    metrics = _extract_metrics(result)
    passed = (
        metrics["mrr"] > _MRR_THRESHOLD
        and metrics["hits_at_10"] > _HITS10_THRESHOLD
    )

    output = {
        "dataset": "FB15k-237",
        "epochs": _SANITY_EPOCHS,
        "embedding_dim": _SANITY_EMBEDDING_DIM,
        "mrr": metrics["mrr"],
        "hits_at_10": metrics["hits_at_10"],
        "mrr_threshold": _MRR_THRESHOLD,
        "hits10_threshold": _HITS10_THRESHOLD,
        "passed": passed,
        "run_at": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    out_dir = repo_root / "data" / "kge" / "sanity-check"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "fb15k_237.json"
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")

    if passed:
        log.info("kge_sanity_check_passed", **metrics)
    else:
        log.error(
            "kge_sanity_check_failed",
            mrr=metrics["mrr"],
            mrr_threshold=_MRR_THRESHOLD,
            hits_at_10=metrics["hits_at_10"],
            hits10_threshold=_HITS10_THRESHOLD,
        )

    return output


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


if __name__ == "__main__":
    from app.config import REPO_ROOT
    result = run_sanity_check(REPO_ROOT)
    print(json.dumps(result, indent=2))
    if not result["passed"]:
        raise SystemExit(1)
