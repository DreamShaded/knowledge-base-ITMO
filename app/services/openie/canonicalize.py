"""
Entity canonicalization via embedding similarity (ADR-0008 §3, Gate 3).

Queries Oxigraph for existing entity labels, embeds them alongside the proposed
entity text, and returns the best-matching URI + cosine similarity.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from app.di.ports import DenseEmbedderPort
    from app.stores.oxigraph import OxigraphStore

_CANON_AUTO = 0.92
_CANON_GREY_LOW = 0.75

# Query all labeled entities across all named graphs (limit to avoid OOM)
_LABEL_SPARQL = """
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT DISTINCT ?entity ?label WHERE {
  {
    GRAPH ?g { ?entity rdfs:label ?label }
  } UNION {
    GRAPH ?g { ?entity skos:prefLabel ?label }
  }
  FILTER(isIRI(?entity))
}
LIMIT 5000
"""


async def find_canonical(
    entity_text: str,
    oxigraph: "OxigraphStore",
    embedder: "DenseEmbedderPort",
    top_k: int = 3,
) -> tuple[str | None, float]:
    """
    Return (canonical_uri, similarity) for the closest existing entity.

    Thresholds:
      >= 0.92 → auto-canonicalize (caller may reuse URI)
      0.75–0.92 → grey zone (caller routes to pending)
      < 0.75 → new entity (return None, similarity)
    """
    if not entity_text.strip():
        return None, 0.0

    rows = oxigraph.sparql_select(_LABEL_SPARQL)
    if not rows:
        return None, 0.0

    # Build deduplicated URI → label map; keep first label per URI
    seen: dict[str, str] = {}
    for row in rows:
        uri = row.get("entity", "")
        label = row.get("label", "") or _label_from_uri(uri)
        if uri and uri not in seen:
            seen[uri] = label

    if not seen:
        return None, 0.0

    uris = list(seen.keys())
    labels = list(seen.values())

    all_texts = [entity_text] + labels
    embeddings = await embedder.embed_passages(all_texts)

    query_vec = np.array(embeddings[0], dtype=np.float32)
    entity_vecs = np.array(embeddings[1:], dtype=np.float32)

    q_norm = float(np.linalg.norm(query_vec))
    if q_norm == 0.0:
        return None, 0.0
    query_vec /= q_norm

    norms = np.linalg.norm(entity_vecs, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1.0, norms)
    entity_vecs /= norms

    sims: np.ndarray = entity_vecs @ query_vec  # (N,)
    best_idx = int(np.argmax(sims))
    best_sim = float(sims[best_idx])
    best_uri = uris[best_idx]

    if best_sim >= _CANON_GREY_LOW:
        return best_uri, best_sim

    return None, best_sim  # below grey zone → treat as new entity


def _label_from_uri(uri: str) -> str:
    """Derive a human-readable label from a URI fragment, path segment, or URN segment."""
    part = uri.rstrip("/")
    if "#" in part:
        fragment = part.split("#")[-1]
    elif "/" in part:
        fragment = part.split("/")[-1]
    else:
        fragment = part.split(":")[-1]  # URN: urn:concept:gradient-descent → "gradient-descent"
    spaced = re.sub(r"([A-Z])", r" \1", fragment).replace("_", " ").replace("-", " ")
    return spaced.strip().lower()
