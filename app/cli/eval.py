"""
`python -m app eval` subcommands (phase-14, ADR-0010).
  run        — execute eval on gold question set
  sentinels  — run sentinel regression cases S01-S10
  ab         — single-variable A/B comparison vs baseline run
  calibrate  — LLM-judge calibration and Cohen's κ
"""
from __future__ import annotations

import json
from pathlib import Path

import click

from app.config import REPO_ROOT

_GOLD_DIR = REPO_ROOT / "evals" / "gold"
_SENTINELS_DIR = REPO_ROOT / "evals" / "sentinels"
_RUNS_BASE = REPO_ROOT / "evals" / "runs"
_DEFAULT_ENDPOINT = "http://localhost:8000/v1/chat/completions"


@click.group(name="eval")
def eval_cli() -> None:
    """Eval gold-set and sentinel runner (phase-14, ADR-0010)."""


@eval_cli.command()
@click.option("--category", default=None, help="Filter by category (e.g. multi_hop, refusal)")
@click.option("--gold", default=str(_GOLD_DIR), show_default=True, help="Gold set directory")
@click.option("--endpoint", default=_DEFAULT_ENDPOINT, show_default=True)
def run(category: str | None, gold: str, endpoint: str) -> None:
    """Execute eval run on the gold question set."""
    from app.services.eval.runner import load_gold, run_eval, make_run_dir
    from app.services.eval.metrics import aggregate_metrics

    questions = load_gold(Path(gold), category)
    if not questions:
        click.secho("No questions found — check gold dir or category filter.", fg="yellow")
        return

    click.echo(f"Running eval: {len(questions)} questions → {endpoint}")
    run_dir = make_run_dir(_RUNS_BASE)
    results = run_eval(questions, endpoint, run_dir)

    metrics = aggregate_metrics(results, questions)
    summary_path = run_dir / "metrics_summary.json"
    summary_path.write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    click.secho(f"\nResults saved: {run_dir}", fg="green")
    _print_metrics(metrics)


@eval_cli.command()
@click.option("--endpoint", default=_DEFAULT_ENDPOINT, show_default=True)
def sentinels(endpoint: str) -> None:
    """Run sentinel regression cases S01-S10 and write sentinel_results.md."""
    from app.services.eval.runner import load_sentinels, run_eval, make_run_dir

    sentinel_list = load_sentinels(_SENTINELS_DIR)
    if not sentinel_list:
        click.secho("No sentinels found in evals/sentinels/.", fg="yellow")
        return

    questions = [
        {"id": s["id"], "query": s["query"], "category": "sentinel"}
        for s in sentinel_list
    ]
    run_dir = make_run_dir(_RUNS_BASE)
    results = run_eval(questions, endpoint, run_dir)

    click.echo(f"\nSentinel results ({len(results)} / {len(sentinel_list)}):")
    ok = err = 0
    for r, s in zip(results, sentinel_list):
        if r.get("status") == "ok":
            ok += 1
            click.echo(f"  [OK]  {s['id']} — {s.get('name', s['id'])}")
        else:
            err += 1
            click.secho(f"  [ERR] {s['id']} — {r.get('error', 'unknown')}", fg="red")

    report = run_dir / "sentinel_results.md"
    _write_sentinel_report(report, results, sentinel_list)
    click.echo(f"\nReport: {report}  (ok={ok}, err={err})")


@eval_cli.command()
@click.option("--variant", required=True, help="Description of the config variant being tested")
@click.option("--baseline", required=True, help="Baseline run_id (timestamp dir under evals/runs/)")
@click.option("--endpoint", default=_DEFAULT_ENDPOINT, show_default=True)
@click.option("--gold", default=str(_GOLD_DIR))
def ab(variant: str, baseline: str, endpoint: str, gold: str) -> None:
    """Single-variable A/B comparison against a baseline run (ADR-0010 §9)."""
    from app.services.eval.runner import load_gold, run_eval, make_run_dir
    from app.services.eval.metrics import aggregate_metrics

    questions = load_gold(Path(gold))
    if not questions:
        click.secho("No gold questions found.", fg="yellow")
        return

    baseline_path = _RUNS_BASE / baseline / "metrics_summary.json"
    if not baseline_path.exists():
        click.secho(f"Baseline not found: {baseline_path}", fg="red")
        return

    run_dir = make_run_dir(_RUNS_BASE)
    results = run_eval(questions, endpoint, run_dir)
    new_metrics = aggregate_metrics(results, questions)
    base_metrics = json.loads(baseline_path.read_text(encoding="utf-8"))

    click.secho(f"\nA/B: {variant!r} vs baseline {baseline}", bold=True)
    click.secho(f"{'Metric':<35} {'Baseline':>10} {'New':>10} {'Δ':>10}")
    click.echo("-" * 68)
    for key in sorted(set(new_metrics) | set(base_metrics)):
        bv = base_metrics.get(key)
        nv = new_metrics.get(key)
        if isinstance(bv, (int, float)) and isinstance(nv, (int, float)):
            delta = f"{nv - bv:+.4f}"
            color = "green" if nv >= bv else "red"
            click.secho(f"  {key:<33} {bv:>10.4f} {nv:>10.4f} {delta:>10}", fg=color)
        else:
            click.echo(f"  {key:<33} {str(bv):>10} {str(nv):>10}")


@eval_cli.command()
def calibrate() -> None:
    """Compute Cohen's κ between author and LLM-judge ratings (ADR-0010 §6)."""
    from app.services.eval.kappa import cohens_kappa, interpret_kappa

    calib_path = REPO_ROOT / "evals" / "baselines" / "judge_calibration.json"
    if not calib_path.exists():
        click.echo("No calibration data at evals/baselines/judge_calibration.json")
        click.echo('Format: {"author": [1,0,1,...], "judge": [1,1,0,...]}')
        return

    data = json.loads(calib_path.read_text(encoding="utf-8"))
    author = data.get("author", [])
    judge = data.get("judge", [])

    if not author:
        click.secho("author or judge arrays are empty.", fg="red")
        return

    try:
        kappa = cohens_kappa(author, judge)
    except ValueError as exc:
        click.secho(str(exc), fg="red")
        return

    label = interpret_kappa(kappa)
    color = "green" if kappa >= 0.6 else "yellow" if kappa >= 0.4 else "red"
    click.secho(f"Cohen's κ = {kappa:.4f}  [{label}]  (n={len(author)})", fg=color)
    if kappa < 0.6:
        click.echo("Target ≥ 0.6 not met — refine rubric and re-annotate a subset.")


# ── helpers ────────────────────────────────────────────────────────────────────

def _print_metrics(metrics: dict) -> None:
    click.secho("Metrics:", bold=True)
    for k, v in metrics.items():
        click.echo(f"  {k}: {v}")


def _write_sentinel_report(path: Path, results: list[dict], sentinels: list[dict]) -> None:
    lines = ["# Sentinel Regression Results\n"]
    for r, s in zip(results, sentinels):
        status = "OK" if r.get("status") == "ok" else "ERROR"
        lines += [
            f"## {s['id']} — {s.get('name', s['id'])}",
            f"- Status: {status}",
            f"- Latency: {r.get('latency_ms', 'N/A')} ms",
            f"- Answer (first 200 chars): {r.get('answer', '')[:200]}",
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")
