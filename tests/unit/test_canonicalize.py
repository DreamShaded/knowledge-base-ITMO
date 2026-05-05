"""Unit tests for OpenIE entity canonicalization (phase-10, ADR-0008 §3 Gate 3)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from app.services.openie.canonicalize import find_canonical, _label_from_uri, _CANON_AUTO, _CANON_GREY_LOW


def _make_embedder(vectors: list[list[float]]) -> MagicMock:
    """Return a mock DenseEmbedderPort that returns pre-set vectors."""
    embedder = MagicMock()
    embedder.embed_passages = AsyncMock(return_value=[list(v) for v in vectors])
    return embedder


def _make_oxigraph(rows: list[dict]) -> MagicMock:
    oxigraph = MagicMock()
    oxigraph.sparql_select = MagicMock(return_value=rows)
    return oxigraph


def _unit(v: list[float]) -> list[float]:
    a = np.array(v, dtype=np.float32)
    return (a / np.linalg.norm(a)).tolist()


# ── label_from_uri ────────────────────────────────────────────────────────────

def test_label_from_uri_fragment():
    assert _label_from_uri("https://my-pkm.local/ontology#CamelCase") == "camel case"


def test_label_from_uri_path_segment():
    assert _label_from_uri("urn:concept:gradient-descent") == "gradient descent"


def test_label_from_uri_trailing_slash():
    assert _label_from_uri("urn:concept:rag/") == "rag"


# ── find_canonical ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_canonical_empty_graph_returns_none():
    oxigraph = _make_oxigraph([])
    embedder = _make_embedder([[1.0, 0.0, 0.0]])
    uri, sim = await find_canonical("neural network", oxigraph, embedder)
    assert uri is None
    assert sim == 0.0


@pytest.mark.asyncio
async def test_find_canonical_high_similarity_returns_uri():
    # Query vec and entity vec are nearly identical → similarity ≈ 1.0
    q_vec = _unit([1.0, 0.5, 0.2])
    e_vec = _unit([1.0, 0.5, 0.2])  # identical

    oxigraph = _make_oxigraph([{"entity": "urn:concept:neural-network", "label": "neural network"}])
    embedder = _make_embedder([q_vec, e_vec])

    uri, sim = await find_canonical("neural network", oxigraph, embedder)
    assert uri == "urn:concept:neural-network"
    assert sim >= _CANON_AUTO


@pytest.mark.asyncio
async def test_find_canonical_grey_zone_returns_uri_with_mid_sim():
    # Cosine ≈ 0.82 → grey zone
    q_vec = _unit([1.0, 0.0, 0.0])
    e_vec = _unit([1.0, 0.6, 0.0])  # angle ≈ 31°, cosine ≈ 0.857 in 2D

    # Make cosine land in [0.75, 0.92]
    q = np.array([1.0, 0.0], dtype=np.float32)
    q /= np.linalg.norm(q)
    e = np.array([1.0, 0.65], dtype=np.float32)
    e /= np.linalg.norm(e)
    sim_val = float(q @ e)
    assert _CANON_GREY_LOW <= sim_val < _CANON_AUTO

    oxigraph = _make_oxigraph([{"entity": "urn:concept:machine-learning", "label": "machine learning"}])
    embedder = _make_embedder([q.tolist(), e.tolist()])

    uri, sim = await find_canonical("ml", oxigraph, embedder)
    assert uri == "urn:concept:machine-learning"
    assert _CANON_GREY_LOW <= sim < _CANON_AUTO


@pytest.mark.asyncio
async def test_find_canonical_below_grey_zone_returns_none_uri():
    q = np.array([1.0, 0.0], dtype=np.float32)
    e = np.array([0.0, 1.0], dtype=np.float32)  # orthogonal, cosine = 0.0

    oxigraph = _make_oxigraph([{"entity": "urn:concept:unrelated", "label": "unrelated"}])
    embedder = _make_embedder([q.tolist(), e.tolist()])

    uri, sim = await find_canonical("completely different", oxigraph, embedder)
    assert uri is None
    assert sim < _CANON_GREY_LOW


@pytest.mark.asyncio
async def test_find_canonical_picks_best_of_multiple():
    q = np.array([1.0, 0.0], dtype=np.float32)
    close = np.array([0.99, 0.14], dtype=np.float32)
    far = np.array([0.0, 1.0], dtype=np.float32)

    close /= np.linalg.norm(close)

    oxigraph = _make_oxigraph([
        {"entity": "urn:concept:close", "label": "close thing"},
        {"entity": "urn:concept:far", "label": "far thing"},
    ])
    embedder = _make_embedder([q.tolist(), close.tolist(), far.tolist()])

    uri, sim = await find_canonical("query", oxigraph, embedder)
    assert uri == "urn:concept:close"


@pytest.mark.asyncio
async def test_find_canonical_empty_text_returns_none():
    oxigraph = _make_oxigraph([{"entity": "urn:concept:x", "label": "x"}])
    embedder = _make_embedder([[1.0, 0.0]])
    uri, sim = await find_canonical("   ", oxigraph, embedder)
    assert uri is None
    assert sim == 0.0
