"""
`python -m app diag` subcommands.
  profile  — show detected profile + effective config diff vs defaults
  versions — show installed component versions from data/.versions.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from app.config import REPO_ROOT, AppConfig
from app.config_profile import VALID_PROFILES, _detect_from_nvidia_smi, _validate_profile_name


@click.group(name="diag")
def diag() -> None:
    """Diagnostics: profile detection, version info."""


@diag.command()
@click.option("--profile", default=None, help="Override profile name (skip autodetect)")
def profile(profile: str | None) -> None:
    """Print detected hardware profile and effective config diff vs defaults."""
    import os

    if profile:
        _validate_profile_name(profile)
        os.environ["KB_PROFILE"] = profile
        active = profile
    else:
        active = os.environ.get("KB_PROFILE") or _detect_from_nvidia_smi()
        _validate_profile_name(active)
        os.environ["KB_PROFILE"] = active

    profile_file = REPO_ROOT / "infra" / "profiles" / f"{active}.yaml"
    if not profile_file.exists():
        click.secho(f"ERROR: profile file not found: {profile_file}", fg="red", err=True)
        sys.exit(1)

    try:
        cfg = AppConfig()
    except Exception as exc:
        click.secho(f"ERROR loading config: {exc}", fg="red", err=True)
        sys.exit(1)

    defaults = AppConfig.model_construct()  # no validation, Python defaults only

    click.secho(f"\nActive profile: {active}", fg="green", bold=True)
    click.echo(f"Profile file:   {profile_file}")
    click.echo(f"Config YAML:    {REPO_ROOT / 'infra' / 'config.yaml'}")
    click.echo()

    _print_diff("embedder", defaults.embedder.model_dump(), cfg.embedder.model_dump())
    _print_diff("reranker", defaults.reranker.model_dump(), cfg.reranker.model_dump())
    _print_diff("marker",   defaults.marker.model_dump(),   cfg.marker.model_dump())
    _print_diff("ollama",   defaults.ollama.model_dump(),   cfg.ollama.model_dump())
    _print_diff("kge",      defaults.kge.model_dump(),      cfg.kge.model_dump())
    _print_diff("playwright", defaults.playwright.model_dump(), cfg.playwright.model_dump())

    click.echo()
    click.secho("Sources:", bold=True)
    if cfg.sources.vaults:
        for v in cfg.sources.vaults:
            click.echo(f"  vault: {v.id} → {v.path}")
    else:
        click.echo("  (no vaults configured — edit infra/config.yaml or set KB_SOURCES__VAULTS__0__PATH)")
    if cfg.sources.book_paths:
        for bp in cfg.sources.book_paths:
            click.echo(f"  book_path: {bp}")
    else:
        click.echo("  (no book_paths configured)")


@diag.command()
def versions() -> None:
    """Print installed component versions from data/.versions.json."""
    versions_file = REPO_ROOT / "data" / ".versions.json"
    if not versions_file.exists():
        click.echo("data/.versions.json not found — run scripts/install.sh first.")
        return

    with versions_file.open() as f:
        data = json.load(f)

    click.secho("\nInstalled versions:", bold=True)
    for component, info in data.items():
        if isinstance(info, dict):
            ver = info.get("version") or info.get("revision") or info.get("digest") or json.dumps(info)
        else:
            ver = str(info)
        click.echo(f"  {component:20s} {ver}")


def _print_diff(section: str, defaults: dict, effective: dict) -> None:
    changed = {k: (defaults.get(k), v) for k, v in effective.items() if defaults.get(k) != v}
    click.secho(f"{section}:", bold=True)
    if not changed:
        click.echo("  (same as defaults)")
        return
    for key, (default_val, effective_val) in changed.items():
        click.echo(f"  {key}: {default_val!r} → {effective_val!r}")
