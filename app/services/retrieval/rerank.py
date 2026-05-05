"""Cross-encoder reranking wrapper for the retrieval pipeline (ADR-0003 §2)."""
from __future__ import annotations

import dataclasses

from app.di.ports import RerankerPort
from app.services.retrieval.aggregate import ScoredChunk


async def rerank_chunks(
    reranker: RerankerPort,
    query: str,
    chunks: list[ScoredChunk],
    top_k: int = 30,
) -> list[ScoredChunk]:
    """Re-score chunks with the cross-encoder; return top_k by new score.

    Qwen3Reranker.rerank() returns ScoredDoc(id=str(original_index), score=…),
    so we recover the original chunk by index and replace its score.
    """
    if not chunks:
        return []

    docs = [c.content for c in chunks]
    scored = await reranker.rerank(query, docs, top_k=top_k)

    result: list[ScoredChunk] = []
    for sd in scored:
        idx = int(sd.id)
        if 0 <= idx < len(chunks):
            result.append(dataclasses.replace(chunks[idx], score=sd.score))

    return result
