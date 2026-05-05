"""
`python -m app kge` subcommands (phase-13 manual override CLI).
  train        — trigger training (--force-full, --with-hpo)
  runs list    — list all KGE training runs
  rollback     — swap current symlink to a history run
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import click

from app.config import REPO_ROOT

_KGE_ROOT = REPO_ROOT / "data" / "kge"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@click.group(name="kge")
def kge() -> None:
    """KGE training management: train, runs, rollback."""


@kge.command()
@click.option("--force-full", is_flag=True, help="Force full retrain (skip tier decision)")
@click.option("--with-hpo", is_flag=True, help="Include HPO grid search")
def train(force_full: bool, with_hpo: bool) -> None:
    """Trigger KGE training manually (bypasses change_score tier logic)."""
    from app.config_profile import load_config
    from app.stores.oxigraph import OxigraphStore
    from app.stores.sqlite import SQLiteStore

    cfg = load_config()
    base = REPO_ROOT / "app" / "services" / "kge"
    trainer = _load("kge_trainer_cli", base / "kge-trainer.py")
    loader = _load("kge_triples_loader_cli", base / "triples-loader.py")
    gate = _load("kge_quality_gate_cli", base / "quality-gate.py")

    sqlite = SQLiteStore(path=str(REPO_ROOT / cfg.sqlite.path))
    sqlite.init()

    # Priority: --with-hpo > --force-full > full (default)
    tier = "hpo" if with_hpo else ("full" if force_full else "full")
    click.echo(f"Starting KGE training: tier={tier}")

    ox = OxigraphStore(path=str(REPO_ROOT / "data" / "oxigraph"))
    triples = loader.load_training_triples(ox)
    if not triples:
        click.secho("No triples found — is the graph populated?", fg="yellow")
        return

    click.echo(f"Loaded {len(triples)} triples")
    old_metrics = gate.read_current_metrics(_KGE_ROOT)

    kge_cfg = {}
    epochs = getattr(getattr(cfg, "kge", None), "epochs", 200)
    training, valid, _test, factory = trainer.build_triples_factory(triples)
    model, metrics = trainer.train_rotate(training, valid, epochs=epochs)
    run_dir, _run_id = trainer.make_run_dir(REPO_ROOT)
    trainer.save_run(run_dir, model, factory, metrics, kge_cfg)

    passed = gate.run_quality_gate(metrics, old_metrics, run_dir, _KGE_ROOT)
    color = "green" if passed else "red"
    status = "PASSED" if passed else "FAILED (old model kept)"
    old_mrr = old_metrics.get("mrr", 0.0)
    new_mrr = metrics.get("mrr", 0.0)
    click.secho(f"Quality gate: {status}", fg=color)
    click.echo(f"MRR: {old_mrr:.4f} → {new_mrr:.4f}")


@kge.group(name="runs")
def runs() -> None:
    """KGE run management."""


@runs.command(name="list")
def runs_list() -> None:
    """List all KGE training runs, marking the current model."""
    rollback = _load("kge_rollback_cli", REPO_ROOT / "app" / "services" / "kge" / "rollback.py")
    all_runs = rollback.list_all_runs(_KGE_ROOT)
    history = rollback.list_history_runs(_KGE_ROOT)

    if not all_runs and not history:
        click.echo("No KGE runs found.")
        return

    click.secho(f"\n{'Run ID':<24} {'MRR':>8}  Status", bold=True)
    for r in all_runs:
        marker = " ← current" if r["current"] else ""
        click.echo(f"  {r['run_id']:<22} {r['mrr']:8.4f}{marker}")

    if history:
        click.secho("\nHistory (rollback targets):", bold=True)
        for r in history:
            click.echo(f"  {r['run_id']:<22} {r['mrr']:8.4f}")


@kge.command()
@click.argument("run_id")
def rollback(run_id: str) -> None:
    """Roll back to a previous KGE model by run_id (atomic symlink swap)."""
    import re
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", run_id):
        click.secho(f"Invalid run_id format: {run_id!r}", fg="red")
        return
    rollback_mod = _load("kge_rollback_rb", REPO_ROOT / "app" / "services" / "kge" / "rollback.py")
    try:
        target = rollback_mod.rollback_to(run_id, _KGE_ROOT)
        # Verify resolved path stays within kge_root (path traversal guard)
        if not target.resolve().is_relative_to(_KGE_ROOT.resolve()):
            click.secho("Rollback target outside kge_root — aborted.", fg="red")
            return
        click.secho(f"Rolled back to {run_id}", fg="green")
        click.echo(f"  → {target}")
        click.echo("Note: predictions will be invalidated on next surfacing run.")
    except FileNotFoundError as exc:
        click.secho(str(exc), fg="red")


@kge.command()
def nightly() -> None:
    """Run the adaptive nightly retrain cycle (change_score → tier decision → train)."""
    from app.config_profile import load_config
    from app.stores.sqlite import SQLiteStore

    cfg = load_config()
    sqlite = SQLiteStore(path=str(REPO_ROOT / cfg.sqlite.path))
    sqlite.init()
    conn = sqlite.connect()

    scheduler = _load(
        "kge_retrain_sched_nightly",
        REPO_ROOT / "app" / "services" / "kge" / "retrain-scheduler.py",
    )

    import yaml
    kge_cfg_path = REPO_ROOT / "infra" / "kge_config.yaml"
    config = yaml.safe_load(kge_cfg_path.read_text(encoding="utf-8")) if kge_cfg_path.exists() else {}

    click.echo("Starting adaptive nightly KGE cycle...")
    tier = scheduler.run_nightly_cycle(conn, None, REPO_ROOT, config)
    click.secho(f"Cycle complete: tier={tier}", fg="green")
    conn.close()
