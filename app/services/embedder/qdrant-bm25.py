"""
Qdrant-native BM25 sparse embedder via FastEmbed (ADR-0003).
Pre-processes text with ru-pymorphy-tokenizer before passing to FastEmbed Bm25,
so Russian morphological variants share the same index terms.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from app.di.ports import SparseVec

_HERE = Path(__file__).parent


def _load_lemmatizer():
    import sys
    name = "ru_pymorphy_tokenizer"
    spec = importlib.util.spec_from_file_location(name, _HERE / "ru-pymorphy-tokenizer.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class QdrantBm25Sparse:
    """
    SparseEmbedderPort implementation using FastEmbed Bm25 + pymorphy3 Russian lemmatization.
    model_name="Qdrant/bm25" → FastEmbed downloads ~10 MB vocab, cached in data/models/.
    """

    MODEL_NAME = "Qdrant/bm25"

    def __init__(self, cache_dir: str = "data/models") -> None:
        self._cache_dir = cache_dir
        self._model = None
        self._lemmatizer = _load_lemmatizer()

    def _ensure(self) -> object:
        if self._model is None:
            from fastembed import SparseTextEmbedding
            self._model = SparseTextEmbedding(
                model_name=self.MODEL_NAME,
                cache_dir=self._cache_dir,
            )
        return self._model

    def _lemmatize(self, text: str) -> str:
        return self._lemmatizer.lemmatize_text(text)

    async def embed(self, texts: list[str]) -> list[SparseVec]:
        import asyncio

        def _do():
            model = self._ensure()
            lemmatized = [self._lemmatize(t) for t in texts]
            results = list(model.embed(lemmatized))
            return [
                SparseVec(indices=r.indices.tolist(), values=r.values.tolist())
                for r in results
            ]

        return await asyncio.to_thread(_do)

    def embed_query(self, text: str) -> SparseVec:
        """Synchronous helper for single-query use."""
        model = self._ensure()
        lemmatized = self._lemmatize(text)
        result = next(model.query_embed(lemmatized))
        return SparseVec(indices=result.indices.tolist(), values=result.values.tolist())
