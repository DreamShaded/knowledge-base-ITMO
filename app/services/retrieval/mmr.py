"""Maximal Marginal Relevance diversity filter (ADR-0002 §4, λ=0.6)."""
from __future__ import annotations

import math

from app.services.retrieval.schemas import Parent

_DEFAULT_LAMBDA = 0.6


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity; returns 0 for zero vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def run_mmr(
    parents: list[Parent],
    top_k: int = 8,
    lambda_: float = _DEFAULT_LAMBDA,
) -> list[Parent]:
    """Select top_k diverse parents via MMR.

    Picks argmax(λ·relevance_score - (1-λ)·max_cosine_to_already_selected)
    at each step. Parents without embeddings use score-only selection (treated
    as orthogonal to all selected items).
    """
    if not parents:
        return []
    if top_k >= len(parents):
        top_k = len(parents)  # select all but still in MMR order

    remaining = list(range(len(parents)))
    selected: list[int] = []

    for _ in range(top_k):
        if not remaining:
            break
        best_idx = -1
        best_score = float("-inf")

        for i in remaining:
            p = parents[i]
            rel = p.score
            if p.embedding and selected:
                max_sim = max(
                    _cosine(p.embedding, parents[j].embedding)
                    for j in selected
                    if parents[j].embedding
                )
            else:
                max_sim = 0.0
            mmr_score = lambda_ * rel - (1 - lambda_) * max_sim
            if mmr_score > best_score:
                best_score = mmr_score
                best_idx = i

        selected.append(best_idx)
        remaining.remove(best_idx)

    return [parents[i] for i in selected]
