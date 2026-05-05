"""
OpenIE async job worker: LLM extraction + 5-gate promotion (ADR-0008 §3, phase-10).

Job payload: {"chunk_id": str, "chunk_text": str, "doc_uri": str}
GPU policy: low priority (nightly batch default; sync on /index --openie).
"""
from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from app.logging import get_logger
from app.services.openie.canonicalize import find_canonical, _CANON_AUTO, _CANON_GREY_LOW
from app.services.openie.gates import (
    gate1_adaptive_confidence,
    gate2_predicate_whitelist,
    gate3_canonicalize,
    gate4_cross_source,
    gate5_sanity,
)
from app.services.openie.routing import route
from app.services.openie.schemas import CandidateTriple, GateResult

if TYPE_CHECKING:
    from app.di.ports import DenseEmbedderPort
    from app.stores.oxigraph import OxigraphStore

log = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "infra" / "prompts"
_WHITELIST_FILE = Path(__file__).parent.parent.parent.parent / "infra" / "graph" / "predicate_whitelist.yaml"

_PKM = "https://my-pkm.local/ontology#"
_TIER1 = "urn:tier1"
_TIER1_PENDING = "urn:tier1-pending"
_TIER1_REJECTED = "urn:tier1-rejected"


@functools.lru_cache(maxsize=1)
def _load_prompt() -> str:
    p = _PROMPTS_DIR / "openie.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


@functools.lru_cache(maxsize=1)
def _load_whitelist() -> frozenset[str]:
    import yaml
    if not _WHITELIST_FILE.exists():
        return frozenset()
    data = yaml.safe_load(_WHITELIST_FILE.read_text(encoding="utf-8")) or {}
    return frozenset(data.get("predicates", []))


async def run_openie_job(
    chunk_id: str,
    chunk_text: str,
    doc_uri: str,
    *,
    oxigraph: "OxigraphStore",
    embedder: "DenseEmbedderPort",
    ollama_host: str,
    model: str = "qwen3:8b",
    openie_priority: int = 1,
    timeout: float = 60.0,
) -> dict[str, int]:
    """
    Extract triples from chunk_text, run 5-gate pipeline, write to Oxigraph.

    Returns counts: {"promoted": N, "pending": N, "rejected": N}.
    """
    candidates = await _extract_candidates(chunk_id, chunk_text, ollama_host, model, timeout)
    if not candidates:
        return {"promoted": 0, "pending": 0, "rejected": 0}

    whitelist = _load_whitelist()
    counts = {"promoted": 0, "pending": 0, "rejected": 0}

    for triple in candidates:
        decision = await _evaluate_triple(triple, whitelist, oxigraph, embedder)
        _write_to_graph(triple, decision, doc_uri, oxigraph)

        dest = decision.destination
        if dest == "tier1":
            counts["promoted"] += 1
        elif dest == "tier1-pending":
            counts["pending"] += 1
        else:
            counts["rejected"] += 1

    log.info(
        "openie_job_done",
        chunk_id=chunk_id,
        **counts,
    )
    return counts


# ── LLM extraction ────────────────────────────────────────────────────────────

async def _extract_candidates(
    chunk_id: str,
    chunk_text: str,
    ollama_host: str,
    model: str,
    timeout: float,
) -> list[CandidateTriple]:
    prompt = _load_prompt().replace("{chunk_text}", chunk_text[:3000])
    if not prompt.strip():
        log.warning("openie_prompt_missing")
        return []

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "Extract triples from the chunk above."},
        ],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 1024},
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{ollama_host}/api/chat", json=payload)
            r.raise_for_status()
            content = r.json().get("message", {}).get("content", "{}")
        raw_triples = json.loads(content).get("triples", [])
        candidates = [_parse_triple(t, chunk_id) for t in raw_triples if isinstance(t, dict)]
        candidates = [c for c in candidates if c is not None]
        log.debug("openie_extracted", chunk_id=chunk_id, count=len(candidates))
        return candidates
    except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
        log.warning("openie_llm_error", chunk_id=chunk_id, error=str(exc))
        return []
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        log.warning("openie_parse_error", chunk_id=chunk_id, error=str(exc))
        return []


def _parse_triple(raw: dict, chunk_id: str) -> CandidateTriple | None:
    subj = str(raw.get("subject_uri", "")).strip()
    pred = str(raw.get("predicate", "")).strip()
    obj = str(raw.get("object_uri_or_literal", "")).strip()
    if not subj or not pred or not obj:
        return None
    conf = float(raw.get("confidence_self_reported", 0.0))
    conf = max(0.0, min(1.0, conf))
    valid_claim_types = {"factual_specific", "factual_general", "framing", "opinion"}
    claim_type = raw.get("claim_type", "factual_general")
    if claim_type not in valid_claim_types:
        claim_type = "factual_general"
    return CandidateTriple(
        subject_uri=subj,
        predicate=pred,
        object_uri_or_literal=obj,
        evidence_chunk_id=chunk_id,
        confidence_self_reported=conf,
        claim_type=claim_type,  # type: ignore[arg-type]
        is_object_literal=bool(raw.get("is_object_literal", False)),
    )


# ── 5-gate evaluation ─────────────────────────────────────────────────────────

async def _evaluate_triple(
    triple: CandidateTriple,
    whitelist: frozenset[str],
    oxigraph: "OxigraphStore",
    embedder: "DenseEmbedderPort",
) -> "app.services.openie.schemas.RoutingDecision":  # type: ignore[name-defined]
    from app.services.openie.schemas import RoutingDecision

    gates: list[GateResult] = []

    # Gate 2 first — cheapest check, most common hard-reject
    g2 = gate2_predicate_whitelist(triple, whitelist)
    gates.append(g2)
    if not g2.passed:
        return RoutingDecision(
            destination="tier1-rejected",
            final_confidence=triple.confidence_self_reported,
            gates=gates,
            reason=f"gate2_reject: {g2.reason}",
        )

    # Gate 5 sanity (cheap, no I/O)
    duplicate = _check_duplicate(triple, oxigraph)
    conflict = _check_conflict(triple, oxigraph)
    g5 = gate5_sanity(triple, duplicate_exists=duplicate, conflict_exists=conflict)
    gates.append(g5)
    if not g5.passed:
        return RoutingDecision(
            destination="tier1-rejected",
            final_confidence=triple.confidence_self_reported,
            gates=gates,
            reason=f"gate5_reject: {g5.reason}",
        )

    # Gate 1 confidence
    g1 = gate1_adaptive_confidence(triple)
    gates.append(g1)

    # Gate 3 canonicalization (needs embedder + Oxigraph)
    subj_canon, subj_sim = await find_canonical(
        _label_for_entity(triple.subject_uri), oxigraph, embedder
    )
    obj_canon, obj_sim = (None, 0.0)
    if not triple.is_object_literal:
        obj_canon, obj_sim = await find_canonical(
            _label_for_entity(triple.object_uri_or_literal), oxigraph, embedder
        )

    # Mark new entities so Gate 1 can raise threshold
    triple = triple.model_copy(update={
        "is_new_subject": subj_canon is None and subj_sim < _CANON_GREY_LOW,
        "is_new_object": obj_canon is None and obj_sim < _CANON_GREY_LOW and not triple.is_object_literal,
    })

    g3 = gate3_canonicalize(triple, subj_canon, subj_sim, obj_canon, obj_sim)
    gates.append(g3)

    # Gate 4 cross-source (count existing identical triples across chunks)
    n_sources = _count_sources(triple, oxigraph)
    g4 = gate4_cross_source(triple, n_sources)
    gates.append(g4)

    return route(triple, gates)


# ── Oxigraph helpers ──────────────────────────────────────────────────────────

def _check_duplicate(triple: CandidateTriple, oxigraph: "OxigraphStore") -> bool:
    """True if the exact (s, p, o) triple already exists in tier1 or tier1-pending."""
    sparql = f"""
    ASK {{
        GRAPH ?g {{
            <{triple.subject_uri}> <{triple.predicate}> <{triple.object_uri_or_literal}> .
        }}
        FILTER(?g IN (<urn:tier1>, <urn:tier1-pending>))
    }}
    """
    try:
        rows = oxigraph.sparql_select(sparql)
        # pyoxigraph ASK returns boolean result
        return bool(rows)
    except Exception:
        return False


def _check_conflict(triple: CandidateTriple, oxigraph: "OxigraphStore") -> bool:
    """Detect semantic conflict: same subject+predicate but different object already exists."""
    sparql = f"""
    ASK {{
        GRAPH ?g {{
            <{triple.subject_uri}> <{triple.predicate}> ?existing .
            FILTER(?existing != <{triple.object_uri_or_literal}>)
        }}
        FILTER(?g IN (<urn:tier0>, <urn:tier1>))
    }}
    """
    try:
        rows = oxigraph.sparql_select(sparql)
        return bool(rows)
    except Exception:
        return False


def _count_sources(triple: CandidateTriple, oxigraph: "OxigraphStore") -> int:
    """Count distinct source chunks that have extracted the same triple."""
    sparql = f"""
    SELECT (COUNT(DISTINCT ?chunk) AS ?n) WHERE {{
        GRAPH ?g {{
            <{triple.subject_uri}> <{triple.predicate}> <{triple.object_uri_or_literal}> ;
                <{_PKM}evidenceChunk> ?chunk .
        }}
        FILTER(?g IN (<urn:tier1>, <urn:tier1-pending>))
    }}
    """
    try:
        rows = oxigraph.sparql_select(sparql)
        return int(rows[0].get("n", "0") or "0") + 1  # +1 for current chunk
    except Exception:
        return 1


# ── Graph write ───────────────────────────────────────────────────────────────

def _write_to_graph(
    triple: CandidateTriple,
    decision: "app.services.openie.schemas.RoutingDecision",  # type: ignore[name-defined]
    doc_uri: str,
    oxigraph: "OxigraphStore",
) -> None:
    if decision.destination == "tier1-rejected":
        log.debug(
            "triple_rejected",
            subj=triple.subject_uri,
            pred=triple.predicate,
            reason=decision.reason,
        )
        return

    graph = _TIER1 if decision.destination == "tier1" else _TIER1_PENDING
    subj = triple.subject_uri
    pred = triple.predicate
    obj = triple.object_uri_or_literal
    chunk = triple.evidence_chunk_id
    conf = f"{decision.final_confidence:.4f}"
    conflict_flag = "true" if decision.conflict else "false"

    if triple.is_object_literal:
        obj_part = f'"{_esc(obj)}"'
    else:
        obj_part = f"<{obj}>"

    ttl = f"""
    <{subj}> <{pred}> {obj_part} .
    <{subj}> <{pred}> {obj_part} .
    <<{subj}> <{pred}> {obj_part}>> <{_PKM}provenance> "{decision.provenance}" .
    <<{subj}> <{pred}> {obj_part}>> <{_PKM}confidence> "{conf}" .
    <<{subj}> <{pred}> {obj_part}>> <{_PKM}source> "openie:v3" .
    <<{subj}> <{pred}> {obj_part}>> <{_PKM}evidenceChunk> <{chunk}> .
    <<{subj}> <{pred}> {obj_part}>> <{_PKM}evidenceDoc> <{doc_uri}> .
    <<{subj}> <{pred}> {obj_part}>> <{_PKM}conflictFlag> "{conflict_flag}" .
    """

    try:
        oxigraph.load_ttl(ttl.strip(), graph)
    except Exception as exc:
        log.warning("openie_write_failed", subj=subj, pred=pred, error=str(exc))


def _label_for_entity(uri: str) -> str:
    """Extract last path segment or fragment for embedding lookup."""
    part = uri.rstrip("/")
    fragment = part.split("#")[-1] if "#" in part else part.split("/")[-1]
    return fragment.replace("-", " ").replace("_", " ")


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
