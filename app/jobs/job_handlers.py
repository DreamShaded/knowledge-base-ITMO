"""
Job handler implementations for phase-02 job types.
Handlers are async functions dispatched by JobWorker.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from app.jobs import types as T
from app.logging import get_logger

if TYPE_CHECKING:
    from app.jobs.worker import WorkerContext

log = get_logger(__name__)


async def dispatch_job(job_type: str, payload: dict, ctx: "WorkerContext") -> None:
    from app.services.indexer import get_indexer_handlers
    index_handlers = get_indexer_handlers()

    handlers = {
        T.PARSE_BOOK:        _handle_parse_book,
        T.SOFT_DELETE_BOOK:  _handle_soft_delete_book,
        T.UPDATE_BOOK_PATHS: _handle_update_book_paths,
        T.SAVE_WEB:          _handle_save_web,
        T.EXTRACT_TIER0:     _handle_extract_tier0,
        T.OPENIE_CANDIDATE:  _handle_openie_candidate,
        **index_handlers,
    }
    handler = handlers.get(job_type)
    if handler is None:
        log.info("job_type_not_implemented", job_type=job_type)
        return
    await handler(payload, ctx)


# ── parse_book ────────────────────────────────────────────────────────────────

async def _handle_parse_book(payload: dict, ctx: "WorkerContext") -> None:
    from app.services.book_storage import BookStorage
    from app.services.pdf_parser import get_parser

    path = payload["path"]
    force_marker: bool = payload.get("force_marker", False)

    if not Path(path).exists():
        raise FileNotFoundError(f"PDF not found: {path}")

    sha256 = _sha256_file(path)
    storage = BookStorage(ctx.data_dir)

    if storage.is_parsed(sha256):
        storage.add_source_path(sha256, path)
        log.info("parse_book_dedup", sha256=sha256[:12], path=path)
        return

    parser = get_parser(path, force_marker=force_marker)
    priority = payload.get("priority", 5)

    # Evict embedder from GPU before loading Marker models
    if ctx.container:
        ctx.container.dense_embedder.unload()

    async with ctx.gpu_mutex.acquire(priority=priority):
        book = await parser.parse(path)

    book.sha256 = sha256
    await storage.save(book, source_path=path, parser_used=type(parser).__name__)
    log.info("parse_book_done", sha256=sha256[:12], chapters=len(book.chapters))


# ── soft_delete_book ──────────────────────────────────────────────────────────

async def _handle_soft_delete_book(payload: dict, ctx: "WorkerContext") -> None:
    from app.services.book_storage import BookStorage

    path = payload.get("path", "")
    sha256 = payload.get("sha256") or ""
    storage = BookStorage(ctx.data_dir)

    if not sha256 and path:
        sha256 = storage.find_sha256_by_path(path) or ""
    if not sha256:
        log.warning("soft_delete_sha256_not_found", path=path)
        return

    if not storage.is_parsed(sha256):
        return

    meta = storage.load_metadata(sha256)

    # Remove deleted path from source_paths
    meta["source_paths"] = [p for p in meta.get("source_paths", []) if p != path]

    if not meta["source_paths"]:
        meta["removed"] = True
        log.info("book_soft_deleted", sha256=sha256[:12])
    else:
        log.info("book_path_removed", sha256=sha256[:12], remaining=len(meta["source_paths"]))

    storage.write_metadata(sha256, meta)


# ── update_book_paths ─────────────────────────────────────────────────────────

async def _handle_update_book_paths(payload: dict, ctx: "WorkerContext") -> None:
    from app.services.book_storage import BookStorage

    old_path: str = payload["old_path"]
    new_path: str = payload["new_path"]
    sha256: str = payload.get("sha256") or ""
    storage = BookStorage(ctx.data_dir)

    if not sha256:
        sha256 = storage.find_sha256_by_path(old_path) or ""
    if not sha256 or not storage.is_parsed(sha256):
        return

    meta = storage.load_metadata(sha256)
    paths: list[str] = meta.get("source_paths", [])

    if old_path in paths:
        paths[paths.index(old_path)] = new_path
    elif new_path not in paths:
        paths.append(new_path)

    meta["source_paths"] = paths
    storage.write_metadata(sha256, meta)
    log.info("book_paths_updated", sha256=sha256[:12])


# ── save_web ──────────────────────────────────────────────────────────────────

async def _handle_save_web(payload: dict, ctx: "WorkerContext") -> None:
    import importlib.util, sys
    from pathlib import Path as _Path
    _p = _Path(__file__).parent.parent / "services" / "web" / "save-web-handler.py"
    if "_save_web_handler" not in sys.modules:
        spec = importlib.util.spec_from_file_location("_save_web_handler", _p)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules["_save_web_handler"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    handler = sys.modules["_save_web_handler"]

    url: str = payload["url"]
    sqlite = ctx.container.sqlite if ctx.container else None  # type: ignore[union-attr]
    dispatcher = ctx.dispatcher
    await handler.save_web_page(
        url=url,
        data_dir=ctx.data_dir,
        sqlite=sqlite,
        dispatcher=dispatcher,
        gpu_mutex=ctx.gpu_mutex,
    )


# ── helpers ───────────────────────────────────────────────────────────────────

async def _handle_extract_tier0(payload: dict, ctx: "WorkerContext") -> None:
    """Re-extract Tier-0 triples for a note without re-embedding."""
    path: str = payload["path"]
    vault_id: str = payload["vault_id"]
    vault_path: str = payload["vault_path"]

    md_path = Path(path)
    if not md_path.exists():
        log.warning("tier0_note_missing", path=path)
        return

    content = md_path.read_text(encoding="utf-8", errors="replace")
    rel_path = str(md_path.relative_to(vault_path))
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    from datetime import datetime, timezone
    modified_at = datetime.fromtimestamp(
        md_path.stat().st_mtime, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    await _emit_note_tier0(ctx, vault_id, vault_path, rel_path, content, content_hash, modified_at)
    log.info("tier0_extracted", vault_id=vault_id, rel_path=rel_path)


async def _emit_note_tier0(
    ctx: "WorkerContext",
    vault_id: str,
    vault_path: str,
    rel_path: str,
    content: str,
    content_hash: str,
    modified_at: str,
) -> None:
    """Delete stale Tier-0 triples for note and emit fresh ones."""
    import importlib.util as ilu
    import sys
    from pathlib import Path as _Path

    _emitter_path = _Path(__file__).parent.parent / "services" / "graph" / "tier0" / "emitter.py"
    _we_path = _Path(__file__).parent.parent / "services" / "graph" / "tier0" / "wikilink-extractor.py"

    if "_tier0_emitter" not in sys.modules:
        spec = ilu.spec_from_file_location("_tier0_emitter", _emitter_path)
        mod = ilu.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules["_tier0_emitter"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    emitter = sys.modules["_tier0_emitter"]

    if "_tier0_we" not in sys.modules:
        spec = ilu.spec_from_file_location("_tier0_we", _we_path)
        mod = ilu.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules["_tier0_we"] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    we = sys.modules["_tier0_we"]

    from app.services.graph.tier0.uri import note_uri
    n_uri = note_uri(vault_id, rel_path)
    notes_index = we.build_notes_index(vault_path, vault_id)

    oxigraph = ctx.container.oxigraph  # type: ignore[union-attr]
    oxigraph.delete_note_triples(n_uri)
    quads = emitter.emit_note_triples(vault_id, rel_path, content, content_hash, modified_at, notes_index)
    oxigraph.add_quads(quads)


async def _handle_openie_candidate(payload: dict, ctx: "WorkerContext") -> None:
    """Run OpenIE extraction + 5-gate promotion for a single chunk."""
    from app.services.openie.worker import run_openie_job

    chunk_id: str = payload["chunk_id"]
    chunk_text: str = payload.get("chunk_text", "")
    doc_uri: str = payload.get("doc_uri", "")
    ollama_host: str = payload.get("ollama_host", "")

    if not chunk_text.strip():
        log.info("openie_skip_empty_chunk", chunk_id=chunk_id)
        return

    if not ollama_host and ctx.container:
        ollama_host = ctx.container.cfg.ollama.host  # type: ignore[union-attr]

    oxigraph = ctx.container.oxigraph  # type: ignore[union-attr]
    embedder = ctx.container.dense_embedder  # type: ignore[union-attr]

    timeout = 60.0
    if ctx.container:
        timeout = ctx.container.cfg.openie.timeout  # type: ignore[union-attr]

    await run_openie_job(
        chunk_id=chunk_id,
        chunk_text=chunk_text,
        doc_uri=doc_uri,
        oxigraph=oxigraph,
        embedder=embedder,
        ollama_host=ollama_host,
        timeout=timeout,
    )


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
