"""
Qwen3 tokenizer wrapper for chunk-boundary token counting.
Lazy-loads AutoTokenizer from HuggingFace; falls back to char/4 estimate in tests.
"""
from __future__ import annotations

from pathlib import Path

_tokenizer = None
_CHARS_PER_TOKEN = 4  # rough fallback estimate


def _load(model_id: str = "Qwen/Qwen3-Embedding-0.6B") -> object:
    global _tokenizer
    if _tokenizer is None:
        from transformers import AutoTokenizer
        from app.config import REPO_ROOT

        cache = str(REPO_ROOT / "data" / "models")
        _tokenizer = AutoTokenizer.from_pretrained(model_id, cache_dir=cache)
    return _tokenizer


class Qwen3Tokenizer:
    """Thin wrapper; counts tokens and splits text respecting token boundaries."""

    def __init__(self, model_id: str = "Qwen/Qwen3-Embedding-0.6B", use_fallback: bool = False) -> None:
        self._model_id = model_id
        self._use_fallback = use_fallback
        self._tok = None

    def _ensure(self) -> None:
        if self._tok is None and not self._use_fallback:
            try:
                self._tok = _load(self._model_id)
            except Exception:
                self._use_fallback = True

    def count(self, text: str) -> int:
        self._ensure()
        if self._use_fallback or self._tok is None:
            return max(1, len(text) // _CHARS_PER_TOKEN)
        return len(self._tok.encode(text, add_special_tokens=False))

    def split_with_overlap(self, text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
        """Split text into chunks of ≤max_tokens with overlap_tokens sliding window."""
        self._ensure()
        if self._use_fallback or self._tok is None:
            return _fallback_split(text, max_tokens, overlap_tokens)

        ids = self._tok.encode(text, add_special_tokens=False)
        if len(ids) <= max_tokens:
            return [text]

        chunks: list[str] = []
        start = 0
        while start < len(ids):
            end = min(start + max_tokens, len(ids))
            chunk_ids = ids[start:end]
            chunks.append(self._tok.decode(chunk_ids, skip_special_tokens=True))
            if end == len(ids):
                break
            start += max_tokens - overlap_tokens
        return chunks


def _fallback_split(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Character-based split used when tokenizer unavailable."""
    max_chars = max_tokens * _CHARS_PER_TOKEN
    step = (max_tokens - overlap_tokens) * _CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return [text]
    chunks, pos = [], 0
    while pos < len(text):
        chunks.append(text[pos: pos + max_chars])
        pos += step
        if pos >= len(text):
            break
    return chunks
