"""CLI: show job queue status — counts by status and recent failures."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import click

from app.config import REPO_ROOT, AppConfig


def _get_db_path(cfg: AppConfig) -> str:
    p = cfg.sqlite.path
    if not Path(p).is_absolute():
        p = str(REPO_ROOT / p)
    return p


def _extract_path(payload_json: str) -> str:
    try:
        p = json.loads(payload_json or "{}")
        return p.get("path") or p.get("url") or ""
    except Exception:
        return ""


@click.command("status")
@click.option("--failed", "show_failed", is_flag=True, default=False,
              help="Show last 20 failed jobs with error details")
def status(show_failed: bool) -> None:
    """Show job queue stats and optionally recent failures."""
    from app.config_profile import load_config
    cfg = load_config()
    db_path = _get_db_path(cfg)

    if not Path(db_path).exists():
        click.echo("No database found — has the server been started yet?")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    counts = {
        r["status"]: r["n"]
        for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
        ).fetchall()
    }

    pending = counts.get("pending", 0)
    running = counts.get("running", 0)
    done    = counts.get("done",    0)
    failed  = counts.get("failed",  0)
    total   = pending + running + done + failed

    click.echo(f"  pending : {pending}")
    click.echo(f"  running : {running}")
    click.echo(f"  done    : {done}")
    click.echo(f"  failed  : {failed}")
    click.echo(f"  total   : {total}")

    if pending > 0:
        by_type = conn.execute(
            "SELECT type, COUNT(*) AS n FROM jobs WHERE status='pending' GROUP BY type ORDER BY n DESC"
        ).fetchall()
        click.echo("\nPending by type:")
        for r in by_type:
            click.echo(f"  {r['type']:<30} {r['n']}")

    if show_failed and failed > 0:
        rows = conn.execute(
            """SELECT id, type, payload_json, error, finished_at
               FROM jobs WHERE status='failed'
               ORDER BY id DESC LIMIT 20"""
        ).fetchall()
        click.echo("\nLast failed jobs:")
        for r in rows:
            path = _extract_path(r["payload_json"])
            click.echo(f"  #{r['id']} [{r['type']}] {path}")
            click.echo(f"    {r['error']}")

    conn.close()
