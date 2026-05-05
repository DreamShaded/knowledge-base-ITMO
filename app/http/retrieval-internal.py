"""
Internal hybrid retrieval endpoint (ADR-0011 task 9).
POST /v1/retrieval/hybrid — dense+sparse RRF fusion via Qdrant Query API.
Debug/internal only; not exposed in production reverse proxy.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.di.ports import ScoredDoc
from app.logging import get_logger

router = APIRouter(prefix="/v1/retrieval", tags=["retrieval-internal"])
log = get_logger(__name__)


class HybridQueryRequest(BaseModel):
    query: str
    vault_scope: list[str] | str = "all"   # list of vault_id slugs or "all"
    channels: list[str] = ["notes"]         # embedding channel for dense query
    top_k: int = 50


class HybridQueryResponse(BaseModel):
    results: list[ScoredDoc]
    total: int


@router.post("/hybrid", response_model=HybridQueryResponse)
async def hybrid_query(req: HybridQueryRequest, request: Request) -> HybridQueryResponse:
    container = request.app.state.container
    store = container.qdrant
    dense_emb = container.dense_embedder
    sparse_emb = container.sparse_embedder

    channel = req.channels[0] if req.channels else "notes"

    # Embed query — dense (with instruct prefix) + sparse (lemmatized)
    dense_vec = await dense_emb.embed_query(req.query, channel=channel)  # type: ignore[call-arg]
    sparse_result = await sparse_emb.embed([req.query])
    sparse_vec = sparse_result[0]

    # Build payload filter
    from qdrant_client.models import (
        Filter, FieldCondition, MatchValue, MatchAny,
        SparseVector, FusionQuery, Fusion,
        Prefetch,
    )

    must_clauses = [
        FieldCondition(key="removed", match=MatchValue(value=False))
    ]
    if req.vault_scope != "all" and isinstance(req.vault_scope, list):
        must_clauses.append(
            FieldCondition(key="vault_id", match=MatchAny(any=req.vault_scope))
        )

    payload_filter = Filter(must=must_clauses)

    try:
        results = store.client.query_points(
            collection_name=store.collection_name,
            prefetch=[
                Prefetch(
                    query=dense_vec,
                    using="dense",
                    filter=payload_filter,
                    limit=req.top_k * 2,
                ),
                Prefetch(
                    query=SparseVector(
                        indices=sparse_vec.indices,
                        values=sparse_vec.values,
                    ),
                    using="sparse",
                    filter=payload_filter,
                    limit=req.top_k * 2,
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=req.top_k,
            with_payload=True,
        )

        scored = [
            ScoredDoc(
                id=str(pt.id),
                score=pt.score,
                payload=pt.payload or {},
            )
            for pt in results.points
        ]
    except Exception as exc:
        log.error("hybrid_query_error", error=str(exc), query=req.query[:80])
        scored = []

    log.info("hybrid_query", query=req.query[:60], returned=len(scored), channel=channel)
    return HybridQueryResponse(results=scored, total=len(scored))
