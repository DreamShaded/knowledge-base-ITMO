"""Indexer package — re-exports handlers from kebab-case module via importlib."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_HERE = Path(__file__).parent


def _load(stem: str):
    import sys
    name = stem.replace("-", "_")
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def get_indexer_handlers():
    mod = _load("indexer-worker")
    return {
        "index_note": mod.handle_index_note,
        "index_book_chapter": mod.handle_index_book_chapter,
        "index_web_saved": mod.handle_index_web_saved,
        "index_removed": mod.handle_index_removed,
    }


__all__ = ["get_indexer_handlers"]
