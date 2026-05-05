"""
Composition root: wires config → stores → governance → providers → app.
Phase-03: Qwen3EmbeddingLocal, QdrantBm25Sparse, Qwen3RerankerLocal replace placeholders.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import AppConfig, REPO_ROOT
from app.di.ports import (
    DenseEmbedderPort,
    KGEPort,
    LLMPort,
    LLMMessage,
    LLMResponse,
    ModelArtifact,
    KGEPrediction,
    PdfParserPort,
    ParsedBook,
    RerankerPort,
    ScoredDoc,
    SparseEmbedderPort,
    SparseVec,
    Vector1024,
)
from app.governance.circuit_breaker import CircuitBreaker
from app.governance.gpu_mutex import GpuMutex
from app.governance.ollama_lifecycle import OllamaLifecycle
from app.jobs.dispatcher import JobDispatcher
from app.jobs.worker import JobWorker, WorkerContext
from app.stores.oxigraph import OxigraphStore
from app.stores.qdrant import QdrantStore
from app.stores.sqlite import SQLiteStore


def _build_web_channel(cfg, sqlite, dense_embedder, gpu_mutex):
    """Build WebChannel from config. Returns None if searxng_host is unset."""
    import importlib.util, sys
    from pathlib import Path
    p = Path(__file__).parent.parent / "services" / "web" / "web-channel.py"
    name = "_web_channel_mod"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, p)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    mod = sys.modules[name]

    from app.services.web.quota import DailyQuota
    from app.services.web.health import WebChannelHealth

    return mod.WebChannel(
        searxng_host=cfg.web.searxng_host,
        sqlite=sqlite,
        dense_embedder=dense_embedder,
        daily_quota=DailyQuota(limit=cfg.web.daily_quota),
        health=WebChannelHealth(),
        sanitization_mode=cfg.web.sanitization_mode,
        search_top_k=cfg.web.search_top_k,
        gpu_mutex=gpu_mutex,
        playwright_priority=cfg.web.playwright_gpu_priority,
    )


# ── Placeholder adapters (replaced in phases 03-12) ──────────────────────────

class _PlaceholderLLM:
    def __init__(self, cfg: Any) -> None:
        self._host = cfg.host
        self._model = cfg.chat_model

    async def complete(self, messages: list[LLMMessage], schema=None, max_tokens=2048) -> LLMResponse:
        import httpx
        payload = {
            "model": self._model,
            "messages": [m.model_dump() for m in messages],
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(f"{self._host}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
        return LLMResponse(content=data["message"]["content"], model=data.get("model", self._model))


class _PlaceholderKGE:
    async def train(self, triples: list[tuple[str, str, str]]) -> ModelArtifact:
        raise NotImplementedError("KGE not yet implemented — phase-12")

    async def predict(self, model: ModelArtifact, top_k: int = 20) -> list[KGEPrediction]:
        raise NotImplementedError("KGE not yet implemented — phase-12")


# ── Container ─────────────────────────────────────────────────────────────────

@dataclass
class AppContainer:
    """Holds all wired dependencies; passed to FastAPI via app.state."""

    cfg: AppConfig

    # stores
    qdrant: QdrantStore
    oxigraph: OxigraphStore
    sqlite: SQLiteStore

    # governance
    gpu_mutex: GpuMutex
    ollama_lifecycle: OllamaLifecycle
    circuit_breakers: dict[str, CircuitBreaker] = field(default_factory=dict)

    # job system (phase-02)
    dispatcher: JobDispatcher = field(default=None)  # type: ignore[assignment]
    job_worker: JobWorker = field(default=None)  # type: ignore[assignment]

    # providers
    llm: LLMPort = field(default_factory=lambda: _PlaceholderLLM(None))  # type: ignore
    dense_embedder: DenseEmbedderPort = field(default=None)  # type: ignore[assignment]
    sparse_embedder: SparseEmbedderPort = field(default=None)  # type: ignore[assignment]
    reranker: RerankerPort = field(default=None)  # type: ignore[assignment]
    kge: KGEPort = field(default_factory=_PlaceholderKGE)
    web_channel: Any = field(default=None)  # WebChannel | None (phase-07)


def build_container(cfg: AppConfig) -> AppContainer:
    """Wire all components from config. Called once at startup."""
    cache_dir = str(REPO_ROOT / "data" / "models")

    qdrant = QdrantStore(
        path=str(REPO_ROOT / cfg.qdrant.path),
        mode=cfg.qdrant.mode,
        url=cfg.qdrant.url,
        collection_version=cfg.qdrant.collection_version,
        dense_size=cfg.qdrant.dense_size,
    )
    oxigraph = OxigraphStore(path=str(REPO_ROOT / cfg.oxigraph.path))
    sqlite = SQLiteStore(path=str(REPO_ROOT / cfg.sqlite.path))

    gpu_mutex = GpuMutex()
    ollama = OllamaLifecycle(host=cfg.ollama.host, model=cfg.ollama.chat_model)
    llm: LLMPort = _PlaceholderLLM(cfg.ollama)  # type: ignore[assignment]

    # Phase-03: real ML adapters
    from app.services.embedder import get_dense_embedder, get_sparse_embedder
    from app.services.reranker import get_reranker

    dense_embedder = get_dense_embedder(
        model_id=cfg.embedder.model_id,
        device=cfg.embedder.device,
        truncate_dim=cfg.embedder.truncate_dim,
        batch_size=cfg.embedder.batch_size,
        cache_dir=cache_dir,
    )
    sparse_embedder = get_sparse_embedder(cache_dir=cache_dir)
    reranker = get_reranker(
        model_id=cfg.reranker.model_id,
        device=cfg.reranker.device,
        batch_size=cfg.reranker.batch_size,
        gpu_mutex=gpu_mutex,
        cache_dir=cache_dir,
    )

    dispatcher = JobDispatcher(sqlite)

    worker_ctx = WorkerContext(
        db_path=str(REPO_ROOT / cfg.sqlite.path),
        data_dir=REPO_ROOT / "data",
        gpu_mutex=gpu_mutex,
        dispatcher=dispatcher,
    )
    job_worker = JobWorker(worker_ctx)

    web_channel = _build_web_channel(cfg, sqlite, dense_embedder, gpu_mutex)

    container = AppContainer(
        cfg=cfg,
        qdrant=qdrant,
        oxigraph=oxigraph,
        sqlite=sqlite,
        gpu_mutex=gpu_mutex,
        ollama_lifecycle=ollama,
        llm=llm,
        dense_embedder=dense_embedder,
        sparse_embedder=sparse_embedder,
        reranker=reranker,
        dispatcher=dispatcher,
        job_worker=job_worker,
        web_channel=web_channel,
    )
    # Give indexer workers access to the full container
    worker_ctx.container = container
    return container
