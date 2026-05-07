"""CLI: initial full scan — enqueue index jobs for all existing notes and books."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import click

from app.config import REPO_ROOT, AppConfig
from app.jobs import types as T


def _get_db_path(cfg: AppConfig) -> str:
    p = cfg.sqlite.path
    if not Path(p).is_absolute():
        p = str(REPO_ROOT / p)
    return p


async def _enqueue_all(cfg: AppConfig, dry_run: bool) -> None:
    from app.di.composition import build_container
    container = build_container(cfg)
    dispatcher = container.dispatcher
    db_path = _get_db_path(cfg)

    notes_queued = 0
    books_queued = 0

    # Notes from vaults
    for vault in cfg.sources.vaults:
        vault_path = Path(vault.path)
        if not vault_path.exists():
            click.echo(f"  SKIP vault {vault.id}: path not found: {vault_path}")
            continue
        md_files = list(vault_path.rglob("*.md"))
        click.echo(f"  Vault '{vault.id}': {len(md_files)} notes → {vault_path}")
        for f in md_files:
            if not dry_run:
                await dispatcher.enqueue(
                    T.INDEX_NOTE,
                    {"path": str(f), "source_type": "vault", "source_id": vault.id},
                    priority=5,
                    idempotency_key=f"index_note:{f.resolve()}",
                )
            notes_queued += 1

    # Books from book_paths
    for bp in cfg.sources.book_paths:
        bp = Path(bp)
        if not bp.exists():
            click.echo(f"  SKIP book_path: path not found: {bp}")
            continue
        pdf_files = list(bp.rglob("*.pdf"))
        click.echo(f"  Books: {len(pdf_files)} PDFs → {bp}")
        for f in pdf_files:
            if not dry_run:
                await dispatcher.enqueue(
                    T.PARSE_BOOK,
                    {"path": str(f), "priority": 5},
                    priority=5,
                    idempotency_key=f"parse_book:{f.resolve()}",
                )
            books_queued += 1

    if dry_run:
        click.echo(f"\nDry run: would enqueue {notes_queued} notes + {books_queued} books")
    else:
        click.echo(f"\nEnqueued: {notes_queued} notes + {books_queued} books")
        # Show queue size
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()
        conn.close()
        click.echo(f"Pending jobs in queue: {row[0]}")


async def _reindex_books(cfg: AppConfig, dry_run: bool) -> None:
    """Dispatch index_book_chapter for all chapters already parsed on disk."""
    from app.config import REPO_ROOT
    from app.di.composition import build_container
    from app.services.book_storage import BookStorage

    container = build_container(cfg)
    dispatcher = container.dispatcher
    storage = BookStorage(REPO_ROOT / "data")
    db_path = _get_db_path(cfg)

    sha256s = storage.list_sha256s()
    click.echo(f"Found {len(sha256s)} parsed books on disk")

    total_chapters = 0
    for sha256 in sha256s:
        chapters = storage.list_chapters(sha256)
        for slug, content in chapters:
            if not dry_run:
                await dispatcher.enqueue(
                    T.INDEX_BOOK_CHAPTER,
                    {"sha256": sha256, "chapter_slug": slug, "content": content, "modified_at": ""},
                    priority=5,
                    idempotency_key=f"index_book_chapter:{sha256}:{slug}",
                )
            total_chapters += 1
        click.echo(f"  {sha256[:12]}: {len(chapters)} chapters")

    if dry_run:
        click.echo(f"\nDry run: would enqueue {total_chapters} chapter index jobs")
    else:
        click.echo(f"\nEnqueued: {total_chapters} chapter index jobs")
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT COUNT(*) FROM jobs WHERE status='pending'").fetchone()
        conn.close()
        click.echo(f"Pending jobs in queue: {row[0]}")


@click.command("scan")
@click.option("--dry-run", is_flag=True, default=False, help="Show what would be enqueued without actually doing it")
def scan(dry_run: bool) -> None:
    """Enqueue index jobs for all existing notes and books (initial scan)."""
    from app.config_profile import load_config
    cfg = load_config()

    click.echo("Sources configured:")
    asyncio.run(_enqueue_all(cfg, dry_run))


@click.command("reindex-books")
@click.option("--dry-run", is_flag=True, default=False, help="Show what would be enqueued without actually doing it")
def reindex_books(dry_run: bool) -> None:
    """Re-enqueue index_book_chapter for all chapters already parsed on disk.

    Use this when books were parsed but not indexed (e.g. after fixing the pipeline).
    Safe to run multiple times — idempotency keys prevent duplicate work.
    """
    from app.config_profile import load_config
    cfg = load_config()

    asyncio.run(_reindex_books(cfg, dry_run))
