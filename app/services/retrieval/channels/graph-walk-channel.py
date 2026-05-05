"""Graph walk retrieval channel via SPARQL over Oxigraph (ADR-0008 §7).

Traverses <urn:tier0> ∪ <urn:tier2> from seed entities using predicate weights.
Re-ranks by α·path_score + (1-α)·cosine(query_embedding, note_embedding).
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.stores.oxigraph import OxigraphStore
    from app.stores.qdrant import QdrantStore

from app.services.retrieval.schemas import GraphPath

# Default predicate weights (config overrides in graph_walk.predicate_weights)
_DEFAULT_WEIGHTS: dict[str, float] = {
    "https://my-pkm.local/ontology#linksTo": 1.0,
    "https://my-pkm.local/ontology#about": 0.9,
    "https://my-pkm.local/ontology#tagged": 0.7,
    "https://my-pkm.local/ontology#project": 0.6,
    "https://my-pkm.local/ontology#author": 0.4,
}

_SPARQL_1HOP = """\
PREFIX pkm: <https://my-pkm.local/ontology#>
SELECT ?seed ?pred ?entity WHERE {{
  VALUES ?seed {{ {seeds} }}
  {{
    GRAPH <urn:tier0> {{ ?seed ?pred ?entity . }}
  }} UNION {{
    GRAPH <urn:tier2> {{ ?seed ?pred ?entity . }}
  }} UNION {{
    GRAPH <urn:tier0> {{ ?entity ?pred ?seed . }}
  }} UNION {{
    GRAPH <urn:tier2> {{ ?entity ?pred ?seed . }}
  }}
  FILTER(isIRI(?entity))
  FILTER(?entity NOT IN ({blacklist}))
}}
"""


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0


def _format_uris(uris: list[str]) -> str:
    return " ".join(f"<{u}>" for u in uris) if uris else "<urn:__none__>"


def _build_query(seed_entities: list[str], hub_blacklist: list[str]) -> str:
    seeds = _format_uris(seed_entities)
    blacklist = _format_uris(hub_blacklist)
    return _SPARQL_1HOP.format(seeds=seeds, blacklist=blacklist)


def _bfs_expand(
    store: "OxigraphStore",
    seeds: list[str],
    hub_blacklist: list[str],
    hops: int,
    predicate_weights: dict[str, float],
) -> dict[str, tuple[list[str], list[str], float]]:
    """BFS expansion up to `hops` depth.

    Returns {entity_uri: (path_entities, path_predicates, path_score)}.
    """
    # entity → (path_entities, path_predicates, score)
    visited: dict[str, tuple[list[str], list[str], float]] = {}
    frontier: list[tuple[str, list[str], list[str], float]] = [
        (s, [s], [], 1.0) for s in seeds
    ]
    blackset = set(hub_blacklist)

    for _hop in range(hops):
        next_frontier: list[tuple[str, list[str], list[str], float]] = []
        if not frontier:
            break
        current_seeds = [f[0] for f in frontier]
        query = _build_query(current_seeds, hub_blacklist)
        try:
            rows = store.sparql_select(query)
        except Exception:
            break

        # Group results by (seed, entity)
        by_seed: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            by_seed[row.get("seed", "")].append(row)

        for seed_uri, seed_path, seed_preds, seed_score in frontier:
            for row in by_seed.get(seed_uri, []):
                entity = row.get("entity", "")
                pred = row.get("pred", "")
                if not entity or entity in blackset or entity in visited:
                    continue
                w = predicate_weights.get(pred, 0.3)
                score = seed_score * w
                new_path = seed_path + [entity]
                new_preds = seed_preds + [pred]
                visited[entity] = (new_path, new_preds, score)
                next_frontier.append((entity, new_path, new_preds, score))
        frontier = next_frontier

    return visited


def _fetch_note_embeddings(
    qdrant: "QdrantStore",
    source_ids: list[str],
) -> dict[str, tuple[str, list[float]]]:
    """Retrieve content + dense vector for the parent chunk of each source_id."""
    if not source_ids:
        return {}
    from qdrant_client.models import Filter, FieldCondition, MatchAny

    filt = Filter(must=[
        FieldCondition(key="source_id", match=MatchAny(any=source_ids)),
        FieldCondition(key="is_parent", match=MatchAny(any=[True])),
    ])
    try:
        res = qdrant.client.scroll(
            collection_name=qdrant.collection_name,
            scroll_filter=filt,
            limit=len(source_ids) * 3,
            with_payload=["content", "source_id"],
            with_vectors=["dense"],
        )
        out: dict[str, tuple[str, list[float]]] = {}
        for pt in res[0]:
            sid = (pt.payload or {}).get("source_id", "")
            if sid and sid not in out:
                vec = (pt.vector or {}).get("dense") or []
                content = (pt.payload or {}).get("content", "")
                out[sid] = (content, vec)
        return out
    except Exception:
        return {}


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
    """Traverse graph from seed entities, fetch note contents, re-rank."""
    if not seed_entities:
        return []

    weights = predicate_weights or _DEFAULT_WEIGHTS
    visited = _bfs_expand(oxigraph, seed_entities, hub_blacklist, hops_budget, weights)
    if not visited:
        return []

    # Map entity URIs to source_ids (strip URI prefix conventions)
    entity_to_source: dict[str, str] = {}
    for entity in visited:
        # Convention: note URIs like <pkm:note:source_id> or bare source_id
        source_id = entity.split(":")[-1] if ":" in entity else entity
        entity_to_source[entity] = source_id

    source_ids = list({v for v in entity_to_source.values() if v})
    note_data = _fetch_note_embeddings(qdrant, source_ids)

    paths: list[GraphPath] = []
    for entity, (path_ents, path_preds, path_score) in visited.items():
        source_id = entity_to_source.get(entity, "")
        content, vec = note_data.get(source_id, ("", []))
        if not content:
            continue
        cosine = _cosine(query_embedding, vec) if vec else 0.0
        combined = alpha * path_score + (1 - alpha) * cosine
        paths.append(GraphPath(
            entities=path_ents,
            predicates=path_preds,
            path_score=combined,
            content=content,
            source_id=source_id,
        ))

    paths.sort(key=lambda p: p.path_score, reverse=True)
    return paths[:top_k]
