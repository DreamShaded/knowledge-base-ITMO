"""
Indexer job handlers: index_note, index_book_chapter, index_web_saved (ADR-0003 task 7).
Workflow: load content → chunk → embed (dense GPU + BM25 CPU parallel) → upsert.
Idempotency guard: skip if content_hash unchanged (cheap path in upsert.py).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from app.logging import get_logger
from app.services.indexer.upsert import soft_delete_source, upsert_chunks

if TYPE_CHECKING:
    from app.jobs.worker import WorkerContext

log = get_logger(__name__)


async def handle_index_note(payload: dict, ctx: "WorkerContext") -> None:
    # Support both path-based dispatch (watchdog) and pre-loaded content dispatch
    if "path" in payload and "content" not in payload:
        await _handle_index_note_from_path(payload, ctx)
        return

    note_id: str = payload["note_id"]
    vault_id: str = payload.get("vault_id", "")
    content: str = payload["content"]
    modified_at: str = payload.get("modified_at", "")
    tags: list[str] = payload.get("tags", [])
    project: str = payload.get("project", "")

    await _index_source(
        ctx=ctx,
        source_id=note_id,
        source_type="note",
        content=content,
        vault_id=vault_id,
        chapter_id="",
        project=project,
        tags=tags,
        trust_tier="tier1",
        modified_at=modified_at,
        channel="notes",
    )
    log.info("index_note_done", note_id=note_id, vault_id=vault_id)


async def _handle_index_note_from_path(payload: dict, ctx: "WorkerContext") -> None:
    """Handle watchdog-dispatched INDEX_NOTE with path + vault source_id."""
    import hashlib
    from datetime import datetime, timezone

    path_str: str = payload["path"]
    vault_id: str = payload.get("source_id", "")  # watchdog sets source_id=vault_id

    note_path = Path(path_str)
    if not note_path.exists():
        log.warning("index_note_path_missing", path=path_str)
        return

    # Resolve vault path from config
    cfg = ctx.container.cfg  # type: ignore[union-attr]
    vault_cfg = next((v for v in cfg.sources.vaults if v.id == vault_id), None)
    if vault_cfg is None:
        log.warning("index_note_vault_not_found", vault_id=vault_id, path=path_str)
        return

    vault_path = str(vault_cfg.path)
    try:
        rel_path = str(note_path.relative_to(vault_cfg.path))
    except ValueError:
        log.warning("index_note_not_in_vault", path=path_str, vault=vault_path)
        return

    content = note_path.read_text(encoding="utf-8", errors="replace")
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    stat = note_path.stat()
    modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    # Extract frontmatter for tags/project (reuse tier0 parser)
    from app.jobs.job_handlers import _emit_note_tier0
    import importlib.util as ilu, sys
    _fp_path = Path(__file__).parent.parent / "graph" / "tier0" / "frontmatter-parser.py"
    if "_fp_idx" not in sys.modules:
        spec = ilu.spec_from_file_location("_fp_idx", _fp_path)
        mod = ilu.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules["_fp_idx"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    fm = sys.modules["_fp_idx"].parse_frontmatter(content)

    note_id = f"{vault_id}:{rel_path}"
    await _index_source(
        ctx=ctx,
        source_id=note_id,
        source_type="note",
        content=content,
        vault_id=vault_id,
        chapter_id="",
        project=fm.project,
        tags=fm.tags,
        trust_tier="tier1",
        modified_at=modified_at,
        channel="notes",
    )

    # Tier-0 graph extraction (same job, after embedding)
    await _emit_note_tier0(ctx, vault_id, vault_path, rel_path, content, content_hash, modified_at)
    log.info("index_note_done", note_id=note_id, vault_id=vault_id)


async def handle_index_book_chapter(payload: dict, ctx: "WorkerContext") -> None:
    sha256: str = payload["sha256"]
    chapter_slug: str = payload["chapter_slug"]
    content: str = payload["content"]
    modified_at: str = payload.get("modified_at", "")

    source_id = f"{sha256}:{chapter_slug}"
    await _index_source(
        ctx=ctx,
        source_id=source_id,
        source_type="book_chapter",
        content=content,
        vault_id="",
        chapter_id=chapter_slug,
        project="",
        tags=[],
        trust_tier="tier0",
        modified_at=modified_at,
        channel="books",
    )
    log.info("index_book_chapter_done", sha256=sha256[:12], chapter=chapter_slug)


async def handle_index_web_saved(payload: dict, ctx: "WorkerContext") -> None:
    url_hash: str = payload["url_hash"]
    content: str = payload["content"]
    vault_id: str = payload.get("vault_id", "")
    modified_at: str = payload.get("modified_at", "")

    await _index_source(
        ctx=ctx,
        source_id=url_hash,
        source_type="web_saved",
        content=content,
        vault_id=vault_id,
        chapter_id="",
        project="",
        tags=[],
        trust_tier="tier2",
        modified_at=modified_at,
        channel="web_saved",
    )
    log.info("index_web_saved_done", url_hash=url_hash[:12])


async def handle_index_removed(payload: dict, ctx: "WorkerContext") -> None:
    """Soft-delete: mark all points for source as removed=true."""
    source_id: str = payload["source_id"]
    store = ctx.container.qdrant  # type: ignore[union-attr]
    await asyncio.to_thread(soft_delete_source, store, source_id)
    log.info("index_removed_done", source_id=source_id)


# ── core indexing pipeline ─────────────────────────────────────────────────────

async def _index_source(
    ctx: "WorkerContext",
    source_id: str,
    source_type: str,
    content: str,
    vault_id: str,
    chapter_id: str,
    project: str,
    tags: list[str],
    trust_tier: str,
    modified_at: str,
    channel: str,
) -> None:
    from app.services.chunker import get_chunker, get_tokenizer

    container = ctx.container  # type: ignore[union-attr]
    store = container.qdrant
    dense_emb = container.dense_embedder
    sparse_emb = container.sparse_embedder

    # Chunk
    tokenizer = get_tokenizer(use_fallback=False)
    chunker = get_chunker(tokenizer)
    doc = chunker.chunk(
        text=content,
        source_id=source_id,
        source_type=source_type,
        vault_id=vault_id,
        chapter_id=chapter_id,
        project=project,
        tags=tags,
        trust_tier=trust_tier,
        modified_at=modified_at,
    )

    all_chunks = [*doc.parents, *doc.children]
    if not all_chunks:
        return

    texts = [c.content for c in all_chunks]

    # Evict Marker models from GPU before embedding to free VRAM
    try:
        from app.services.pdf_parser.marker_parser import unload_models as _unload_marker
        _unload_marker()
    except Exception:
        pass

    # Parallel: dense (GPU) + BM25 (CPU)
    dense_task = asyncio.create_task(dense_emb.embed_passages(texts))
    sparse_task = asyncio.create_task(sparse_emb.embed(texts))
    dense_vecs, sparse_vecs = await asyncio.gather(dense_task, sparse_task)

    written = await asyncio.to_thread(upsert_chunks, store, all_chunks, dense_vecs, sparse_vecs)
    log.info(
        "chunks_indexed",
        source_id=source_id,
        total=len(all_chunks),
        written=written,
        skipped=len(all_chunks) - written,
    )
