"""Unit tests for MMR diversity filter (ADR-0002 §4)."""
import math

import pytest

from app.services.retrieval.mmr import run_mmr, _cosine
from app.services.retrieval.schemas import Parent, SourceMeta


def _meta(source_type: str = "note") -> SourceMeta:
    return SourceMeta(
        source_type=source_type, source_id="s1", vault_id="v1",
        trust_tier="tier0", channel="notes",
    )


def _parent(pid: str, score: float, emb: list[float]) -> Parent:
    p = Parent(parent_id=pid, content="x", score=score, source_meta=_meta())
    p = p.model_copy(update={"embedding": emb})
    return p


class TestCosine:
    def test_identical_vectors(self):
        v = [1.0, 0.0, 0.0]
        assert _cosine(v, v) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors(self):
        assert _cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_zero_vector_returns_zero(self):
        assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


class TestMMR:
    def test_returns_top_k(self):
        parents = [_parent(f"p{i}", 1.0 - i * 0.1, [1.0, 0.0]) for i in range(10)]
        result = run_mmr(parents, top_k=3)
        assert len(result) == 3

    def test_empty_input(self):
        assert run_mmr([], top_k=5) == []

    def test_fewer_than_k_returns_all(self):
        parents = [_parent("p1", 0.9, [1.0, 0.0]), _parent("p2", 0.8, [0.0, 1.0])]
        result = run_mmr(parents, top_k=10)
        assert len(result) == 2

    def test_diversity_over_pure_relevance(self):
        """MMR should prefer a diverse lower-scored item over a near-duplicate."""
        # p1: high relevance, embedding pointing right
        # p2: slightly lower relevance, same direction as p1 (near-duplicate)
        # p3: lower relevance, orthogonal direction (diverse)
        p1 = _parent("p1", 1.0, [1.0, 0.0])
        p2 = _parent("p2", 0.95, [0.99, 0.01])   # near-duplicate of p1
        p3 = _parent("p3", 0.80, [0.0, 1.0])      # diverse

        result = run_mmr([p1, p2, p3], top_k=2, lambda_=0.6)
        ids = [r.parent_id for r in result]
        assert "p1" in ids
        # p3 should beat p2 in second slot due to diversity penalty on p2
        assert "p3" in ids
        assert "p2" not in ids

    def test_no_embeddings_falls_back_to_score(self):
        """Parents without embeddings are selected by score only (max_sim=0)."""
        parents = [
            _parent("p1", 1.0, []),
            _parent("p2", 0.9, []),
            _parent("p3", 0.8, []),
        ]
        result = run_mmr(parents, top_k=2)
        assert result[0].parent_id == "p1"
        assert result[1].parent_id == "p2"

    def test_lambda_0_maximises_diversity(self):
        """λ=0 picks purely for diversity (min similarity to selected)."""
        p1 = _parent("p1", 1.0, [1.0, 0.0])
        p2 = _parent("p2", 0.95, [1.0, 0.0])   # same direction as p1
        p3 = _parent("p3", 0.5, [0.0, 1.0])    # orthogonal

        result = run_mmr([p1, p2, p3], top_k=2, lambda_=0.0)
        ids = [r.parent_id for r in result]
        assert "p1" in ids
        assert "p3" in ids   # p3 chosen over p2 because it's orthogonal

    def test_order_is_mmr_greedy_not_score_sorted(self):
        """First item is the highest-scored; second differs from pure score order."""
        p1 = _parent("p1", 1.0, [1.0, 0.0])
        p2 = _parent("p2", 0.9, [1.0, 0.0])   # duplicate direction
        p3 = _parent("p3", 0.6, [0.0, 1.0])   # diverse

        result = run_mmr([p1, p2, p3], top_k=3)
        assert result[0].parent_id == "p1"
        # p3 should appear before p2 in the result
        pos_p2 = next(i for i, r in enumerate(result) if r.parent_id == "p2")
        pos_p3 = next(i for i, r in enumerate(result) if r.parent_id == "p3")
        assert pos_p3 < pos_p2
