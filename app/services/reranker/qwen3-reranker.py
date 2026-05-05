"""
Qwen3-Reranker cross-encoder via HuggingFace transformers (ADR-0003).
Lazy-loaded; GPU-serialized via GpuMutex with reranker priority label.
Batching controlled by RERANKER_BATCH_SIZE config knob (default 50).
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from app.config import REPO_ROOT
from app.di.ports import ScoredDoc
from app.logging import get_logger

log = get_logger(__name__)

_RERANKER_PRIORITY = 10  # same as RAG, reranker is part of retrieval pipeline


class Qwen3RerankerLocal:
    """RerankerPort implementation using Qwen3-Reranker cross-encoder."""

    def __init__(
        self,
        model_id: str = "Qwen/Qwen3-Reranker-0.6B",
        device: str = "cpu",
        batch_size: int = 50,
        gpu_mutex=None,
        cache_dir: Optional[str] = None,
    ) -> None:
        self._model_id = model_id
        self._device = device
        self._batch_size = batch_size
        self._gpu_mutex = gpu_mutex
        self._cache_dir = cache_dir or str(REPO_ROOT / "data" / "models")
        self._tokenizer = None
        self._model = None
        self._load_lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        if self._load_lock is None:
            self._load_lock = asyncio.Lock()
        return self._load_lock

    async def _ensure(self):
        """Async-safe lazy loader with double-checked locking."""
        if self._model is not None:
            return self._tokenizer, self._model
        async with self._get_lock():
            if self._model is None:
                from transformers import AutoTokenizer, AutoModelForSequenceClassification
                import torch
                _t = time.monotonic()
                log.info("reranker_loading", model_id=self._model_id, device=self._device)
                self._tokenizer = AutoTokenizer.from_pretrained(
                    self._model_id, cache_dir=self._cache_dir
                )
                if self._tokenizer.pad_token is None:
                    self._tokenizer.pad_token = self._tokenizer.eos_token
                self._model = AutoModelForSequenceClassification.from_pretrained(
                    self._model_id, cache_dir=self._cache_dir, num_labels=1
                )
                self._model.config.pad_token_id = self._tokenizer.pad_token_id
                self._model.to(self._device)
                self._model.eval()
                log.info("reranker_ready", model_id=self._model_id, latency_ms=int((time.monotonic() - _t) * 1000))
        return self._tokenizer, self._model

    def _score_batch(self, query: str, docs: list[str], tokenizer, model) -> list[float]:
        import torch
        pairs = [(query, doc) for doc in docs]
        enc = tokenizer(
            pairs,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=512,
        )
        enc = {k: v.to(self._device) for k, v in enc.items()}
        with torch.no_grad():
            logits = model(**enc).logits.squeeze(-1)
        return logits.cpu().tolist()

    async def rerank(self, query: str, docs: list[str], top_k: int = 10) -> list[ScoredDoc]:
        if not docs:
            return []

        tokenizer, model = await self._ensure()
        _t = time.monotonic()
        all_scores: list[float] = []
        for i in range(0, len(docs), self._batch_size):
            batch = docs[i: i + self._batch_size]
            if self._gpu_mutex and self._device != "cpu":
                async with self._gpu_mutex.acquire(priority=_RERANKER_PRIORITY):
                    scores = await asyncio.to_thread(self._score_batch, query, batch, tokenizer, model)
            else:
                scores = await asyncio.to_thread(self._score_batch, query, batch, tokenizer, model)
            all_scores.extend(scores)

        ranked = sorted(
            enumerate(zip(docs, all_scores)), key=lambda x: x[1][1], reverse=True
        )
        result = [
            ScoredDoc(id=str(idx), score=score, payload={"content": doc})
            for idx, (doc, score) in ranked[:top_k]
        ]
        log.info(
            "rerank_done",
            in_count=len(docs),
            out_count=len(result),
            device=self._device,
            latency_ms=int((time.monotonic() - _t) * 1000),
        )
        return result
