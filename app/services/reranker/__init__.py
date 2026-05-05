"""Reranker package — re-exports from kebab-case module via importlib."""
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


def get_reranker(model_id: str, device: str, batch_size: int, gpu_mutex=None, cache_dir: str = ""):
    mod = _load("qwen3-reranker")
    kwargs = dict(model_id=model_id, device=device, batch_size=batch_size, gpu_mutex=gpu_mutex)
    if cache_dir:
        kwargs["cache_dir"] = cache_dir
    return mod.Qwen3RerankerLocal(**kwargs)


__all__ = ["get_reranker"]
