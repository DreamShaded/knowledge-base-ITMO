"""
Qdrant upsert helpers: build PointStructs from chunks and write to collection.
Idempotency: point_id is stable_hash(source_id || chunk_index); skip if content_hash matches.
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from app.services.chunker.base import Chunk, ParentChunk

if TYPE_CHECKING:
    from app.stores.qdrant import QdrantStore
    from app.di.ports import SparseVec, Vector1024


def stable_point_id(chunk_id: str) -> int:
    """Convert 16-char hex chunk_id to uint64 for Qdrant."""
    return int(chunk_id[:16], 16)


def _build_payload(c: Chunk | ParentChunk) -> dict:
    return {
        "chunk_id": c.chunk_id,
        "parent_id": c.parent_id,
        "source_type": c.source_type,
        "source_id": c.source_id,
        "vault_id": c.vault_id,
        "chapter_id": c.chapter_id,
        "project": c.project,
        "tags": c.tags,
        "trust_tier": c.trust_tier,
        "content": c.content,
        "content_hash": c.content_hash,
        "modified_at": c.modified_at,
        "removed": c.removed,
        "is_parent": isinstance(c, ParentChunk),
    }


def upsert_chunks(
    store: "QdrantStore",
    chunks: list[Chunk | ParentChunk],
    dense_vecs: list["Vector1024"],
    sparse_vecs: list["SparseVec"],
) -> int:
    """
    Upsert chunks into Qdrant. Returns count of points actually written.
    Skips points where content_hash in existing payload matches incoming hash.
    """
    from qdrant_client.models import PointStruct, SparseVector

    client = store.client
    col = store.collection_name

    if not chunks:
        return 0

    # Fetch existing payloads for these point IDs to check content_hash
    ids = [stable_point_id(c.chunk_id) for c in chunks]
    existing = {}
    try:
        results = client.retrieve(col, ids=ids, with_payload=["content_hash"])
        for r in results:
            existing[r.id] = (r.payload or {}).get("content_hash", "")
    except Exception:
        pass  # first-time index: nothing exists yet

    points = []
    for chunk, dvec, svec in zip(chunks, dense_vecs, sparse_vecs):
        pid = stable_point_id(chunk.chunk_id)
        if existing.get(pid) == chunk.content_hash:
            continue  # unchanged — skip

        points.append(PointStruct(
            id=pid,
            vector={
                "dense": dvec,
                "sparse": SparseVector(
                    indices=svec.indices,
                    values=svec.values,
                ),
            },
            payload=_build_payload(chunk),
        ))

    if points:
        client.upsert(collection_name=col, points=points)

    return len(points)


def soft_delete_source(store: "QdrantStore", source_id: str) -> None:
    """Mark all points for a source as removed=true via payload update."""
    from qdrant_client.models import Filter, FieldCondition, MatchValue, PayloadField

    client = store.client
    col = store.collection_name
    client.set_payload(
        collection_name=col,
        payload={"removed": True},
        points=Filter(
            must=[FieldCondition(key="source_id", match=MatchValue(value=source_id))]
        ),
    )
