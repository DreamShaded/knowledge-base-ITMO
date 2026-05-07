"""
`python -m app links` subcommands.
  discover  — run semantic link discovery for all configured vaults
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import click

from app.config import REPO_ROOT


def _load_discoverer():
    name = "_link_discoverer_cli"
    if name in sys.modules:
        return sys.modules[name]
    path = REPO_ROOT / "app" / "services" / "semantic-links" / "link-discoverer.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@click.group(name="links")
def links_cli() -> None:
    """Semantic link discovery between notes."""


@links_cli.command()
@click.option("--threshold", default=0.75, show_default=True, help="Minimum cosine similarity [0–1]")
@click.option("--top-k", default=5, show_default=True, help="Candidates per note")
@click.option("--vault", "vault_filter", default=None, help="Limit to one vault id")
def discover(threshold: float, top_k: int, vault_filter: str | None) -> None:
    """Find semantically similar note pairs and store as review candidates."""
    from app.config_profile import load_config
    from app.stores.qdrant import QdrantStore
    from app.stores.sqlite import SQLiteStore

    cfg = load_config()

    sqlite = SQLiteStore(path=str(REPO_ROOT / cfg.sqlite.path))
    sqlite.init()

    qdrant = QdrantStore(
        path=str(REPO_ROOT / cfg.qdrant.path),
        mode=cfg.qdrant.mode,
        url=cfg.qdrant.url,
        collection_version=cfg.qdrant.collection_version,
        dense_size=cfg.qdrant.dense_size,
    )
    try:
        qdrant.init()
    except RuntimeError as exc:
        if "already accessed" in str(exc):
            click.secho(
                "Qdrant locked — web server is running.\n"
                "Use the HTTP endpoint instead: POST http://localhost:8000/review/links/discover\n"
                "Or click «Запустить поиск» on http://localhost:8000/review/links",
                fg="red",
            )
            return
        raise

    vaults = cfg.sources.vaults
    if vault_filter:
        vaults = [v for v in vaults if v.id == vault_filter]
        if not vaults:
            click.secho(f"Vault '{vault_filter}' not found in config", fg="red")
            return

    if not vaults:
        click.secho("No vaults configured", fg="yellow")
        return

    discoverer = _load_discoverer()
    conn = sqlite.connect()
    try:
        for vault in vaults:
            click.echo(f"Scanning vault: {vault.id} ({vault.label or vault.path})")
            count = discoverer.discover_for_vault(vault, qdrant, conn, threshold, top_k)
            click.secho(f"  → {count} new candidates", fg="green" if count else "yellow")
    finally:
        conn.close()

    click.secho("Done. Open http://localhost:8000/review/links to review.", fg="green")
