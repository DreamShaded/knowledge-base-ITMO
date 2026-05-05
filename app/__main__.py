"""
Entry point for `python -m app <command>`.
Commands:
  run   — start FastAPI server on :8000
  diag  — diagnostics subcommands (profile, versions)
  kge   — KGE training management (train, runs, rollback)
  eval  — eval gold-set and sentinel runner
"""
from __future__ import annotations

import click
import uvicorn

from app.cli.diag import diag
from app.cli.kge import kge
from app.cli.eval import eval_cli
from app.cli.scan import scan
from app.cli.status import status


@click.group()
def cli() -> None:
    """Knowledge Base CLI."""


@cli.command()
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=8000, show_default=True)
@click.option("--reload", is_flag=True, default=False, help="Dev auto-reload (do not use in prod)")
def run(host: str, port: int, reload: bool) -> None:
    """Start the FastAPI server."""
    uvicorn.run(
        "app:build_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


cli.add_command(diag)
cli.add_command(kge)
cli.add_command(eval_cli, name="eval")
cli.add_command(scan)
cli.add_command(status)

if __name__ == "__main__":
    cli()
