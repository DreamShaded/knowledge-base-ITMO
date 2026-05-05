"""
Qwen3-Embedding dense embedder via sentence-transformers (ADR-0003).
Supports per-channel instruct-prefix templates loaded from infra/prompts/.
MRL-truncation to 1024 dims applied when model's native dim exceeds truncate_dim.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from typing import Optional

from app.config import REPO_ROOT
from app.di.ports import Vector1024
from app.logging import get_logger

log = get_logger(__name__)

_PROMPTS_DIR = REPO_ROOT / "infra" / "prompts"
_CHANNELS = ("notes", "books", "web_saved", "web")

# Instruction prefix used for passages (no per-channel instruct; empty per Qwen3 card)
_PASSAGE_PROMPT = ""


def _load_prompts() -> dict[str, str]:
    """Load per-channel query prompts from infra/prompts/embed_query_<channel>.txt."""
    prompts: dict[str, str] = {}
    for ch in _CHANNELS:
        p = _PROMPTS_DIR / f"embed_query_{ch}.txt"
        if p.exists():
            raw = p.read_text(encoding="utf-8").strip()
            # Strip the "{query}" placeholder → ST uses the text as a prefix
            prompts[ch] = raw.replace("{query}", "").rstrip()
    return prompts


def compute_template_hash(prompts: dict[str, str]) -> str:
    """sha256 of concatenated prompts in canonical order (notes, books, web_saved, web)."""
    combined = "".join(prompts.get(ch, "") for ch in _CHANNELS)
    return hashlib.sha256(combined.encode()).hexdigest()


class Qwen3EmbeddingLocal:
    """
    DenseEmbedderPort implementation.
    model_id: "Qwen/Qwen3-Embedding-0.6B" (laptop) or "Qwen/Qwen3-Embedding-4B" (desktop).
    truncate_dim=1024 on all profiles; 4B output (4096-dim) is MRL-truncated to 1024.
    """

    def __init__(
        self,
        model_id: str = "Qwen/Qwen3-Embedding-0.6B",
        device: str = "cuda",
        truncate_dim: int = 1024,
        batch_size: int = 16,
        cache_dir: Optional[str] = None,
    ) -> None:
        self._model_id = model_id
        self._device = device
        self._truncate_dim = truncate_dim
        self._batch_size = batch_size
        self._cache_dir = cache_dir or str(REPO_ROOT / "data" / "models")
        self._model = None
        self._prompts: dict[str, str] = {}
        self.template_hash: str = ""
        self._load_lock: asyncio.Lock | None = None  # created lazily inside event loop

    def _get_lock(self) -> asyncio.Lock:
        if self._load_lock is None:
            self._load_lock = asyncio.Lock()
        return self._load_lock

    async def _ensure(self):
        """Async-safe lazy loader with double-checked locking to prevent parallel GPU loads."""
        if self._model is not None:
            return self._model
        async with self._get_lock():
            if self._model is None:
                from sentence_transformers import SentenceTransformer
                _t = time.monotonic()
                log.info("embedder_loading", model_id=self._model_id, device=self._device)
                self._prompts = _load_prompts()
                self.template_hash = compute_template_hash(self._prompts)
                self._model = await asyncio.to_thread(self._load_model, SentenceTransformer)
                log.info("embedder_ready", model_id=self._model_id, device=self._device, latency_ms=int((time.monotonic() - _t) * 1000))
        return self._model

    def _is_cached(self) -> bool:
        """Return True if model snapshot exists in local cache dir."""
        safe_id = self._model_id.replace("/", "--")
        return (Path(self._cache_dir) / f"models--{safe_id}").exists()

    def _load_model(self, SentenceTransformer):
        """Load model: offline if cached (avoids HF Hub timeout), else download.
        Falls back to CPU on CUDA OOM."""
        local_files_only = self._is_cached()
        if local_files_only:
            log.info("embedder_offline_mode", model_id=self._model_id)

        def _st(device: str) -> object:
            return SentenceTransformer(
                self._model_id,
                device=device,
                cache_folder=self._cache_dir,
                prompts=self._prompts,
                trust_remote_code=True,
                local_files_only=local_files_only,
            )

        try:
            return _st(self._device)
        except Exception as exc:
            if "OutOfMemoryError" in type(exc).__name__ or "CUDA out of memory" in str(exc):
                log.warning("embedder_cuda_oom_fallback_cpu", model_id=self._model_id, error=str(exc)[:200])
                self._device = "cpu"
                return _st("cpu")
            raise

    def _truncate(self, vec: list[float]) -> Vector1024:
        return Vector1024(vec[: self._truncate_dim])

    def unload(self) -> None:
        """Evict model from GPU; reloads lazily on next embed call."""
        if self._model is not None:
            del self._model
            self._model = None
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:
                pass
            log.info("embedder_unloaded", model_id=self._model_id)

    async def warmup(self) -> None:
        """Load model eagerly — call once at startup to avoid OOM on first parallel request."""
        await self._ensure()

    async def embed_query(self, text: str, channel: str = "notes") -> Vector1024:
        model = await self._ensure()
        _t = time.monotonic()
        vecs = await self._encode_with_oom_retry(
            model, [text],
            prompt_name=channel if channel in self._prompts else None,
            batch_size=1,
        )
        log.debug("embed_query_done", channel=channel, latency_ms=int((time.monotonic() - _t) * 1000))
        return self._truncate(vecs[0].tolist())

    async def embed_passages(self, texts: list[str]) -> list[Vector1024]:
        model = await self._ensure()
        _t = time.monotonic()
        vecs = await self._encode_with_oom_retry(model, texts, batch_size=self._batch_size)
        log.debug("embed_passages_done", count=len(texts), latency_ms=int((time.monotonic() - _t) * 1000))
        return [self._truncate(v.tolist()) for v in vecs]

    async def _encode_with_oom_retry(self, model, texts: list, **kwargs):
        """Encode texts; on CUDA OOM free cache and retry at batch_size=1, then CPU fallback."""
        def _do(m, batch_size: int | None = None):
            kw = dict(kwargs)
            if batch_size is not None:
                kw["batch_size"] = batch_size
            return m.encode(texts, normalize_embeddings=True, convert_to_numpy=True, **kw)

        try:
            return await asyncio.to_thread(_do, model)
        except Exception as exc:
            if not ("OutOfMemoryError" in type(exc).__name__ or "out of memory" in str(exc).lower()):
                raise

        # OOM → free reserved CUDA cache, retry at batch_size=1
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass
        log.warning("embed_oom_retry_after_cache_clear", texts=len(texts))
        try:
            return await asyncio.to_thread(_do, model, 1)
        except Exception as exc2:
            if not ("OutOfMemoryError" in type(exc2).__name__ or "out of memory" in str(exc2).lower()):
                raise

        # Still OOM → reload on CPU
        log.warning("embed_oom_fallback_cpu")
        self.unload()
        self._device = "cpu"
        model_cpu = await self._ensure()
        return await asyncio.to_thread(_do, model_cpu, 1)
