"""Embedder package — re-exports adapters loaded from kebab-case modules via importlib."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_HERE = Path(__file__).parent


def _load(stem: str):
    import sys
    name = stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def get_dense_embedder(model_id: str, device: str, truncate_dim: int, batch_size: int, cache_dir: str = ""):
    mod = _load("qwen3-embedding")
    kwargs = dict(model_id=model_id, device=device, truncate_dim=truncate_dim, batch_size=batch_size)
    if cache_dir:
        kwargs["cache_dir"] = cache_dir
    return mod.Qwen3EmbeddingLocal(**kwargs)


def get_sparse_embedder(cache_dir: str = "data/models"):
    mod = _load("qdrant-bm25")
    return mod.QdrantBm25Sparse(cache_dir=cache_dir)


def get_ru_lemmatizer():
    return _load("ru-pymorphy-tokenizer")


__all__ = ["get_dense_embedder", "get_sparse_embedder", "get_ru_lemmatizer"]
