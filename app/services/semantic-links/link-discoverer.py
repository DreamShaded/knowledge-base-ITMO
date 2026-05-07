"""
Semantic link discovery: finds similar note pairs within each vault via Qdrant dense search,
filters out already-linked pairs, and stores candidates in semantic_link_candidates.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from app.logging import get_logger

if TYPE_CHECKING:
    import sqlite3
    from app.config import VaultConfig
    from app.stores.qdrant import QdrantStore

log = get_logger(__name__)

_WIKILINK_RE = re.compile(r'\[\[([^\]|#\n]+?)(?:[|#][^\]]*)?\]\]')

DEFAULT_THRESHOLD = 0.75
DEFAULT_TOP_K = 5


def _existing_link_stems(content: str) -> set[str]:
    """Extract lowercased stems from all [[wikilinks]] in note content."""
    return {m.group(1).strip().lower() for m in _WIKILINK_RE.finditer(content)}


def _note_stem(source_id: str) -> str:
    """'personal:folder/Note Title.md' → 'note title'"""
    rel = source_id.split(":", 1)[1] if ":" in source_id else source_id
    return Path(rel).stem.lower()


def _scroll_vault_parents(
    qdrant: "QdrantStore",
    vault_id: str,
) -> list:
    """Fetch all parent chunks for a vault. Returns list of ScoredPoint-like objects."""
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    filt = Filter(must=[
        FieldCondition(key="vault_id", match=MatchValue(value=vault_id)),
        FieldCondition(key="source_type", match=MatchValue(value="note")),
        FieldCondition(key="is_parent", match=MatchValue(value=True)),
        FieldCondition(key="removed", match=MatchValue(value=False)),
    ])

    all_points = []
    offset = None
    while True:
        points, next_offset = qdrant.client.scroll(
            collection_name=qdrant.collection_name,
            scroll_filter=filt,
            limit=200,
            offset=offset,
            with_payload=True,
            with_vectors=["dense"],
        )
        all_points.extend(points)
        if next_offset is None:
            break
        offset = next_offset

    return all_points


def _search_similar(
    qdrant: "QdrantStore",
    vault_id: str,
    dense_vec: list[float],
    top_k: int,
    threshold: float,
) -> list:
    """Search for similar parent notes in the same vault."""
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    filt = Filter(must=[
        FieldCondition(key="vault_id", match=MatchValue(value=vault_id)),
        FieldCondition(key="source_type", match=MatchValue(value="note")),
        FieldCondition(key="is_parent", match=MatchValue(value=True)),
        FieldCondition(key="removed", match=MatchValue(value=False)),
    ])

    result = qdrant.client.query_points(
        collection_name=qdrant.collection_name,
        query=dense_vec,
        using="dense",
        query_filter=filt,
        limit=top_k + 1,  # +1: result includes the note itself
        with_payload=["source_id", "content"],
        score_threshold=threshold,
    )
    return result.points


def discover_for_vault(
    vault: "VaultConfig",
    qdrant: "QdrantStore",
    conn: "sqlite3.Connection",
    threshold: float = DEFAULT_THRESHOLD,
    top_k: int = DEFAULT_TOP_K,
) -> int:
    """
    Discover semantic link candidates for one vault.
    Returns count of newly inserted candidates.
    """
    log.info("link_discovery_start", vault=vault.id)

    points = _scroll_vault_parents(qdrant, vault.id)
    if not points:
        log.info("link_discovery_no_notes", vault=vault.id)
        return 0

    # Build: source_id → (point_id, dense_vec, content)
    note_map: dict[str, tuple[int, list[float], str]] = {}
    for pt in points:
        sid = (pt.payload or {}).get("source_id", "")
        vec = (pt.vector or {}).get("dense") if isinstance(pt.vector, dict) else None
        if sid and vec:
            note_map[sid] = (pt.id, vec, (pt.payload or {}).get("content", ""))

    log.info("link_discovery_notes_loaded", vault=vault.id, count=len(note_map))

    seen_pairs: set[tuple[str, str]] = set()
    candidates: list[tuple[str, str, str, float]] = []

    for source_id, (point_id, dense_vec, content) in note_map.items():
        existing = _existing_link_stems(content)

        try:
            hits = _search_similar(qdrant, vault.id, dense_vec, top_k, threshold)
        except Exception as exc:
            log.warning("link_discovery_search_error", source=source_id, error=str(exc))
            continue

        for hit in hits:
            target_id = (hit.payload or {}).get("source_id", "")
            if not target_id or target_id == source_id or hit.id == point_id:
                continue

            pair = (min(source_id, target_id), max(source_id, target_id))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)

            # Skip if forward link already exists
            if _note_stem(target_id) in existing:
                continue

            # Skip if reverse link already exists
            target_content = note_map.get(target_id, (None, None, ""))[2]
            if _note_stem(source_id) in _existing_link_stems(target_content):
                continue

            candidates.append((vault.id, source_id, target_id, hit.score))

    # Insert, skipping duplicates (the unique index on min/max pair also guards this)
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    inserted = 0
    for vault_id, src, tgt, score in candidates:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO semantic_link_candidates
                   (vault_id, source_note_id, target_note_id, score, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (vault_id, src, tgt, score, now),
            )
            inserted += 1
        except Exception as exc:
            log.debug("link_candidate_insert_skip", src=src, tgt=tgt, error=str(exc))

    conn.commit()
    log.info("link_discovery_done", vault=vault.id, candidates=inserted)
    return inserted


def discover_all_vaults(
    vaults: list["VaultConfig"],
    qdrant: "QdrantStore",
    conn: "sqlite3.Connection",
    threshold: float = DEFAULT_THRESHOLD,
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, int]:
    """Run discovery for all configured vaults. Returns {vault_id: inserted_count}."""
    results = {}
    for vault in vaults:
        results[vault.id] = discover_for_vault(vault, qdrant, conn, threshold, top_k)
    return results
