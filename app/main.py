"""
FastAPI application factory.
Lifespan: init stores → load ontology → start worker → start watchdog → yield → stop.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.config import REPO_ROOT
from app.di.composition import AppContainer


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    container: AppContainer = app.state.container
    cfg = container.cfg

    # Init stores
    container.qdrant.init()
    container.oxigraph.init()
    container.sqlite.init()

    # Load ontology
    from app.services.ontology_loader import load_ontology
    load_ontology(container.oxigraph, REPO_ROOT / "docs" / "ontology.ttl")

    # Start job worker
    container.job_worker.start()

    # Schema mismatch detection (ADR-0011): compare qdrant_schema record vs current config
    _check_schema_on_startup(container)

    # Pre-warm ML models to avoid GPU OOM on first parallel request
    embedder = container.dense_embedder
    if hasattr(embedder, "warmup"):
        await embedder.warmup()

    # Start watchdog
    from app.jobs.watchdog import Watchdog
    watchdog = Watchdog(cfg, container.dispatcher)
    app.state.watchdog = watchdog
    watchdog.start()

    yield

    # Shutdown (reverse order)
    watchdog.stop()
    await container.job_worker.stop()


def _check_schema_on_startup(container: AppContainer) -> None:
    """Write schema record on first run; warn if config drifted from stored record."""
    cfg = container.cfg
    sqlite_path = str(REPO_ROOT / cfg.sqlite.path)

    # Resolve template hash from the dense embedder if already loaded, else compute directly
    try:
        from app.services.embedder import get_dense_embedder as _gde  # noqa: F401
        import importlib.util as _ilu
        from pathlib import Path as _P
        _spec = _ilu.spec_from_file_location(
            "qwen3_embedding",
            _P(__file__).parent / "services" / "embedder" / "qwen3-embedding.py",
        )
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
        prompts = _mod._load_prompts()
        template_hash = _mod.compute_template_hash(prompts)
    except Exception:
        template_hash = ""

    store = container.qdrant
    store.write_schema_record(
        sqlite_path=sqlite_path,
        embed_model_id=cfg.embedder.model_id,
        mrl_dim=cfg.embedder.truncate_dim,
        instruct_template_hash=template_hash,
    )
    store.check_schema_mismatch(
        sqlite_path=sqlite_path,
        embed_model_id=cfg.embedder.model_id,
        mrl_dim=cfg.embedder.truncate_dim,
        instruct_template_hash=template_hash,
    )


def create_app(container: AppContainer | None = None) -> FastAPI:
    if container is None:
        from app.config_profile import load_config
        from app.di.composition import build_container
        cfg = load_config()
        container = build_container(cfg)

    from app.logging import configure_logging
    configure_logging(
        log_dir=REPO_ROOT / "data" / "logs",
        level=container.cfg.log.level,
        log_llm_full=container.cfg.log.log_llm_full,
    )

    app = FastAPI(
        title="Knowledge Base",
        version="0.1.0",
        description="Semantic PKM: hybrid retrieval over Obsidian vaults and PDF library",
        lifespan=_lifespan,
    )
    app.state.container = container

    import importlib.util as _ilu
    from pathlib import Path as _P

    from app.http.healthz import router as healthz_router
    from app.http.sparql_readonly import router as sparql_router
    from app.http.preview.notes import router as notes_preview_router
    from app.http.preview.books import router as books_preview_router
    from app.http.preview.web import router as web_preview_router
    from app.http.tier1_review import router as tier1_review_router

    _spec = _ilu.spec_from_file_location(
        "retrieval_internal",
        _P(__file__).parent / "http" / "retrieval-internal.py",
    )
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
    retrieval_router = _mod.router

    _spec2 = _ilu.spec_from_file_location(
        "chat_completions_openai",
        _P(__file__).parent / "http" / "chat-completions-openai.py",
    )
    _mod2 = _ilu.module_from_spec(_spec2)
    _spec2.loader.exec_module(_mod2)  # type: ignore[union-attr]
    chat_completions_router = _mod2.router

    # phase-12: KGE review queue + actions
    _spec3 = _ilu.spec_from_file_location(
        "kge_review_queue",
        _P(__file__).parent / "http" / "review" / "kge-review-queue.py",
    )
    _mod3 = _ilu.module_from_spec(_spec3)
    _spec3.loader.exec_module(_mod3)  # type: ignore[union-attr]
    kge_queue_router = _mod3.router

    _spec4 = _ilu.spec_from_file_location(
        "kge_review_actions",
        _P(__file__).parent / "http" / "review" / "kge-review-actions.py",
    )
    _mod4 = _ilu.module_from_spec(_spec4)
    _spec4.loader.exec_module(_mod4)  # type: ignore[union-attr]
    kge_actions_router = _mod4.router

    # phase-12: SPARQL console UI
    _spec5 = _ilu.spec_from_file_location(
        "sparql_console",
        _P(__file__).parent / "http" / "sparql-console.py",
    )
    _mod5 = _ilu.module_from_spec(_spec5)
    _spec5.loader.exec_module(_mod5)  # type: ignore[union-attr]
    sparql_console_router = _mod5.router

    _spec6 = _ilu.spec_from_file_location(
        "jobs_dashboard",
        _P(__file__).parent / "http" / "jobs-dashboard.py",
    )
    _mod6 = _ilu.module_from_spec(_spec6)
    _spec6.loader.exec_module(_mod6)  # type: ignore[union-attr]
    jobs_dashboard_router = _mod6.router

    app.include_router(healthz_router)
    app.include_router(sparql_router)
    app.include_router(retrieval_router)
    app.include_router(chat_completions_router)
    app.include_router(notes_preview_router)
    app.include_router(books_preview_router)
    app.include_router(web_preview_router)
    app.include_router(tier1_review_router)
    app.include_router(kge_queue_router)
    app.include_router(kge_actions_router)
    app.include_router(sparql_console_router)
    app.include_router(jobs_dashboard_router)

    return app
