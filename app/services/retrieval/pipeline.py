"""Hybrid retrieval pipeline orchestrator (ADR-0002, phase-05, phase-07).

Stages: parallel channel retrieval → cross-encoder rerank → child→parent
aggregation → trust multipliers → web channel (conditional) → MMR → RetrievalResult.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

from app.logging import get_logger
from app.services.retrieval.aggregate import ScoredChunk, aggregate_children
from app.services.retrieval.channels import (
    retrieve_books,
    retrieve_graph_walk,
    retrieve_notes,
    retrieve_web_saved,
    retrieve_wikidata,
)
from app.services.retrieval.mmr import run_mmr
from app.services.retrieval.rerank import rerank_chunks
from app.services.retrieval.schemas import EmptyRetrieval, Parent, RetrievalResult
from app.services.retrieval.trust import apply_trust_multipliers

if TYPE_CHECKING:
    from app.config import GraphWalkConfig
    from app.di.ports import DenseEmbedderPort, RerankerPort, SparseEmbedderPort
    from app.services.web.web_channel import WebChannel
    from app.stores.oxigraph import OxigraphStore
    from app.stores.qdrant import QdrantStore

_HERE = Path(__file__).parent


def _load_web_trigger():
    name = "_web_trigger"
    if name in sys.modules:
        return sys.modules[name]
    p = Path(__file__).parent.parent / "web" / "web-trigger.py"
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _load_web_channel_mod():
    name = "_web_channel_mod"
    if name in sys.modules:
        return sys.modules[name]
    p = Path(__file__).parent.parent / "web" / "web-channel.py"
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod

log = get_logger(__name__)

_CHANNEL_TOP_K = 50        # per-channel child chunks
_MERGE_TOP_K = 150         # combined pool before rerank
_RERANK_TOP_K = 30         # after cross-encoder
_DEFAULT_RESULT_K = 8      # final MMR output


@dataclass
class RetrievalPipeline:
    qdrant: "QdrantStore"
    oxigraph: "OxigraphStore"
    dense_embedder: "DenseEmbedderPort"
    sparse_embedder: "SparseEmbedderPort"
    reranker: "RerankerPort"
    graph_walk_cfg: "GraphWalkConfig"
    web_channel: Optional["WebChannel"] = field(default=None)

    async def retrieve(
        self,
        query: str,
        vault_scope: list[str] | str = "all",
        seed_entities: list[str] | None = None,
        channel_weights: dict[str, float] | None = None,
        top_k: int = _DEFAULT_RESULT_K,
        intent: str = "lookup",
        force_web: bool = False,
        no_web: bool = False,
        sensitive_entities: list[str] | None = None,
    ) -> Union[RetrievalResult, EmptyRetrieval]:
        """Run the full hybrid retrieval pipeline for a query."""
        weights = channel_weights or {}
        _t0 = time.monotonic()
        log.info(
            "retrieval_start",
            query=query[:60],
            intent=intent,
            top_k=top_k,
            weights={k: round(v, 2) for k, v in weights.items()},
        )

        # Step 1: embed query per channel + sparse
        notes_dense, books_dense, web_dense, sparse_results = await asyncio.gather(
            self.dense_embedder.embed_query(query, channel="notes"),
            self.dense_embedder.embed_query(query, channel="books"),
            self.dense_embedder.embed_query(query, channel="web_saved"),
            self.sparse_embedder.embed([query]),
        )
        sparse_vec = sparse_results[0]
        log.debug("embed_done", latency_ms=int((time.monotonic() - _t0) * 1000))

        _t_channels = time.monotonic()
        # Step 2: parallel channel retrieval
        results = await asyncio.gather(
            retrieve_notes(self.qdrant, notes_dense, sparse_vec, vault_scope, _CHANNEL_TOP_K),
            retrieve_books(self.qdrant, books_dense, sparse_vec, _CHANNEL_TOP_K),
            retrieve_web_saved(self.qdrant, web_dense, sparse_vec, _CHANNEL_TOP_K),
            retrieve_graph_walk(
                self.oxigraph,
                self.qdrant,
                list(notes_dense),
                seed_entities or [],
                self.graph_walk_cfg.predicate_weights,
                self.graph_walk_cfg.hub_blacklist,
                self.graph_walk_cfg.hops_budget,
            ),
            retrieve_wikidata(query),
            return_exceptions=True,
        )

        notes_chunks, books_chunks, web_chunks, graph_paths_raw, wikidata_raw = (
            r if not isinstance(r, Exception) else [] for r in results
        )
        graph_paths = graph_paths_raw if isinstance(graph_paths_raw, list) else []
        wikidata_entities = wikidata_raw if isinstance(wikidata_raw, list) else []
        log.info(
            "channel_results",
            notes=len(notes_chunks or []),
            books=len(books_chunks or []),
            web_saved=len(web_chunks or []),
            graph=len(graph_paths),
            wikidata=len(wikidata_entities),
            latency_ms=int((time.monotonic() - _t_channels) * 1000),
        )

        # Step 3: merge Qdrant channels, apply channel weights, take top-150
        all_chunks: list[ScoredChunk] = []
        for channel, chunks in (
            ("notes", notes_chunks),
            ("books", books_chunks),
            ("web_saved", web_chunks),
        ):
            w = weights.get(channel, 1.0)
            for c in (chunks or []):
                all_chunks.append(ScoredChunk(**{**c.__dict__, "score": c.score * w}))

        all_chunks.sort(key=lambda c: c.score, reverse=True)
        all_chunks = all_chunks[:_MERGE_TOP_K]

        # Step 4: cross-encoder rerank top-150 → top-30
        _pre_rerank = len(all_chunks)
        _t_rerank = time.monotonic()
        if all_chunks:
            all_chunks = await rerank_chunks(
                self.reranker, query, all_chunks, top_k=_RERANK_TOP_K
            )
            log.info(
                "rerank_done",
                in_count=_pre_rerank,
                out_count=len(all_chunks),
                latency_ms=int((time.monotonic() - _t_rerank) * 1000),
            )

        # Step 5: child → parent aggregation + promotion
        parents = aggregate_children(all_chunks) if all_chunks else []
        if all_chunks:
            _promoted = sum(1 for p in parents if p.parent_id.startswith(("note:", "chapter:")))
            log.info("aggregate_done", parents=len(parents), promoted=_promoted)

        # Step 6: fill parent content + embeddings from Qdrant for MMR
        if parents:
            parents = await self._fill_parents(parents, all_chunks)

        # Step 7: optional web channel — triggered by multi-signal heuristics
        # NOTE: web trigger runs BEFORE empty check so /web works even with no local sources
        web_parents: list[Parent] = []
        if self.web_channel:
            trigger_mod = _load_web_trigger()
            _web_triggered = trigger_mod.should_trigger_web(
                force_web=force_web,
                no_web=no_web,
                intent=intent,
                parents=parents,
                graph_paths=graph_paths,
                router_entities=seed_entities or [],
            )
            log.info(
                "web_trigger_decision",
                triggered=_web_triggered,
                force_web=force_web,
                no_web=no_web,
                intent=intent,
                local_parents=len(parents),
                top_score=round(parents[0].score, 3) if parents else 0.0,
            )
            if _web_triggered:
                try:
                    web_parents = await self.web_channel.search_and_extract(
                        query,
                        sensitive_entities=sensitive_entities or [],
                        intent=intent,
                    )
                except Exception as exc:
                    log.warning("web_channel_error", error=str(exc))

        # Early exit: nothing from any channel
        if not parents and not web_parents and not graph_paths and not wikidata_entities:
            log.info("retrieval_empty", query=query[:60])
            return EmptyRetrieval()

        # Step 8: trust multipliers on all parents
        parents = apply_trust_multipliers(parents + web_parents)

        # Step 9: MMR diversity → top-K
        _pre_mmr = len(parents)
        parents = run_mmr(parents, top_k=top_k)
        log.info("mmr_done", in_count=_pre_mmr, out_count=len(parents))

        _total_ms = int((time.monotonic() - _t0) * 1000)
        log.info(
            "retrieval_done",
            query=query[:60],
            parents=len(parents),
            graph_paths=len(graph_paths),
            latency_ms=_total_ms,
        )
        return RetrievalResult(
            query=query,
            parents=parents,
            graph_paths=graph_paths,
            wikidata_entities=wikidata_entities,
        )

    async def _fill_parents(
        self,
        parents: list[Parent],
        source_chunks: list[ScoredChunk],
    ) -> list[Parent]:
        """Fetch parent content from Qdrant and compute embeddings for MMR."""
        from app.services.indexer.upsert import stable_point_id

        # Build child_id → embedding map via batch retrieve
        child_ids = [stable_point_id(c.chunk_id) for c in source_chunks if c.chunk_id]
        vec_map: dict[int, list[float]] = {}
        if child_ids:
            try:
                pts = self.qdrant.client.retrieve(
                    self.qdrant.collection_name,
                    ids=child_ids,
                    with_payload=False,
                    with_vectors=["dense"],
                )
                for pt in pts:
                    vec = (pt.vector or {}).get("dense") or []
                    if vec:
                        vec_map[pt.id] = vec
            except Exception:
                pass

        # Build chunk_id → embedding lookup
        chunk_vec: dict[str, list[float]] = {
            c.chunk_id: vec_map.get(stable_point_id(c.chunk_id), [])
            for c in source_chunks if c.chunk_id
        }

        # Fetch real parent content for non-promoted parents
        regular_ids = [
            p.parent_id for p in parents
            if not p.parent_id.startswith(("note:", "chapter:"))
        ]
        content_map: dict[str, str] = {}
        if regular_ids:
            try:
                pts = self.qdrant.client.retrieve(
                    self.qdrant.collection_name,
                    ids=[stable_point_id(pid) for pid in regular_ids],
                    with_payload=["content", "chunk_id"],
                    with_vectors=False,
                )
                for pt in pts:
                    cid = (pt.payload or {}).get("chunk_id", "")
                    if cid:
                        content_map[cid] = (pt.payload or {}).get("content", "")
            except Exception:
                pass

        result: list[Parent] = []
        for p in parents:
            # Compute mean embedding from matched children
            child_vecs = [
                chunk_vec[m.chunk_id]
                for m in p.child_matches
                if m.chunk_id in chunk_vec and chunk_vec[m.chunk_id]
            ]
            if child_vecs:
                dim = len(child_vecs[0])
                mean_emb = [
                    sum(v[i] for v in child_vecs) / len(child_vecs)
                    for i in range(dim)
                ]
            else:
                mean_emb = []

            # Fill content for regular (non-promoted) parents
            content = p.content
            if not p.parent_id.startswith(("note:", "chapter:")):
                fetched = content_map.get(p.parent_id, "")
                if fetched:
                    content = fetched

            result.append(p.model_copy(update={"content": content, "embedding": mean_emb}))

        return result
