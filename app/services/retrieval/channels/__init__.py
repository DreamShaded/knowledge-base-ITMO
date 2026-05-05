"""Retrieval channels: notes, books, web_saved (Qdrant RRF) + wikidata stub.

Graph walk channel lives in graph-walk-channel.py (loaded via importlib).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from app.di.ports import SparseVec, Vector1024
from app.services.retrieval.aggregate import ScoredChunk
from app.services.retrieval.schemas import GraphPath, WikidataEntity

if TYPE_CHECKING:
    from app.stores.oxigraph import OxigraphStore
    from app.stores.qdrant import QdrantStore

_HERE = Path(__file__).parent


# ── Qdrant base ───────────────────────────────────────────────────────────────

def _point_to_scored_chunk(pt) -> ScoredChunk:
    p = pt.payload or {}
    return ScoredChunk(
        chunk_id=p.get("chunk_id", str(pt.id)),
        parent_id=p.get("parent_id", ""),
        source_type=p.get("source_type", ""),
        source_id=p.get("source_id", ""),
        vault_id=p.get("vault_id", ""),
        chapter_id=p.get("chapter_id", ""),
        trust_tier=p.get("trust_tier", ""),
        content=p.get("content", ""),
        score=pt.score,
        tags=p.get("tags") or [],
    )


async def _hybrid_query(
    store: "QdrantStore",
    dense_vec: Vector1024,
    sparse_vec: SparseVec,
    extra_filters: list,
    top_k: int,
) -> list[ScoredChunk]:
    from qdrant_client.models import (
        FieldCondition,
        Filter,
        Fusion,
        FusionQuery,
        MatchValue,
        Prefetch,
        SparseVector,
    )

    must = [
        FieldCondition(key="removed", match=MatchValue(value=False)),
        FieldCondition(key="is_parent", match=MatchValue(value=False)),
        *extra_filters,
    ]
    filt = Filter(must=must)
    try:
        res = store.client.query_points(
            collection_name=store.collection_name,
            prefetch=[
                Prefetch(query=list(dense_vec), using="dense", filter=filt, limit=top_k * 2),
                Prefetch(
                    query=SparseVector(indices=sparse_vec.indices, values=sparse_vec.values),
                    using="sparse",
                    filter=filt,
                    limit=top_k * 2,
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=top_k,
            with_payload=True,
        )
        return [_point_to_scored_chunk(pt) for pt in res.points]
    except Exception:
        return []


# ── Per-source channels ───────────────────────────────────────────────────────

async def retrieve_notes(
    store: "QdrantStore",
    dense_vec: Vector1024,
    sparse_vec: SparseVec,
    vault_scope: list[str] | str,
    top_k: int = 50,
) -> list[ScoredChunk]:
    """Note chunks with optional vault_scope filter (ADR-0012)."""
    from qdrant_client.models import FieldCondition, MatchAny, MatchValue

    filters = [FieldCondition(key="source_type", match=MatchValue(value="note"))]
    if vault_scope != "all" and isinstance(vault_scope, list) and vault_scope:
        filters.append(FieldCondition(key="vault_id", match=MatchAny(any=vault_scope)))
    return await _hybrid_query(store, dense_vec, sparse_vec, filters, top_k)


async def retrieve_books(
    store: "QdrantStore",
    dense_vec: Vector1024,
    sparse_vec: SparseVec,
    top_k: int = 50,
) -> list[ScoredChunk]:
    """Book chapter chunks — cross-vault by design."""
    from qdrant_client.models import FieldCondition, MatchValue

    filters = [FieldCondition(key="source_type", match=MatchValue(value="book_chapter"))]
    return await _hybrid_query(store, dense_vec, sparse_vec, filters, top_k)


async def retrieve_web_saved(
    store: "QdrantStore",
    dense_vec: Vector1024,
    sparse_vec: SparseVec,
    top_k: int = 50,
) -> list[ScoredChunk]:
    """Web-saved chunks — cross-vault by design."""
    from qdrant_client.models import FieldCondition, MatchValue

    filters = [FieldCondition(key="source_type", match=MatchValue(value="web_saved"))]
    return await _hybrid_query(store, dense_vec, sparse_vec, filters, top_k)


async def retrieve_wikidata(query: str) -> list[WikidataEntity]:  # noqa: ARG001
    """Wikidata enrichment stub — replaced in phase-11."""
    return []


# ── Graph walk (loaded via importlib to keep kebab-case filename) ─────────────

def _load_graph_walk():
    name = "_graph_walk_channel"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / "graph-walk-channel.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


async def retrieve_graph_walk(
    oxigraph: "OxigraphStore",
    qdrant: "QdrantStore",
    query_embedding: list[float],
    seed_entities: list[str],
    predicate_weights: dict[str, float],
    hub_blacklist: list[str],
    hops_budget: int = 1,
    alpha: float = 0.6,
    top_k: int = 20,
) -> list[GraphPath]:
    mod = _load_graph_walk()
    return await mod.retrieve_graph_walk(
        oxigraph=oxigraph,
        qdrant=qdrant,
        query_embedding=query_embedding,
        seed_entities=seed_entities,
        predicate_weights=predicate_weights,
        hub_blacklist=hub_blacklist,
        hops_budget=hops_budget,
        alpha=alpha,
        top_k=top_k,
    )


__all__ = [
    "retrieve_notes",
    "retrieve_books",
    "retrieve_web_saved",
    "retrieve_wikidata",
    "retrieve_graph_walk",
]
