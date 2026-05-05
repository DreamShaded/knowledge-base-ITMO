"""
Two-step Wikidata disambiguation (ADR-0008 §4 task 2).
Step 1: wbsearchentities → top-5 candidates.
Step 2: confidence = string_match×0.6 + type_match×0.25 + context_overlap×0.15
Auto-link if >0.7; LLM-disambig if 0.5–0.7 (Ollama required); skip if <0.5.
LLM fail-mode: skip (no auto-link, left for /review) — per open question in plan.
Privacy: only surface form and Wikidata QIDs sent; no note context (PRD §7.3).
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

import httpx

from app.services.wikidata import client as wd_client
from app.services.wikidata.schemas import (
    DisambiguationResult,
    EntityCandidate,
    WikidataSearchResult,
)
from app.logging import get_logger

log = get_logger(__name__)

_AUTO_THRESHOLD = 0.7
_LLM_THRESHOLD = 0.5

# Type-hint → description keywords used for heuristic P31 match
_TYPE_KEYWORDS: dict[str, set[str]] = {
    "Person": {"human", "person", "author", "writer", "scientist", "philosopher", "engineer"},
    "Software": {"software", "programming", "application", "tool", "framework", "library"},
    "Book": {"book", "novel", "publication", "written", "work", "literature"},
    "Organization": {"organization", "company", "institute", "foundation", "association"},
    "Event": {"event", "conference", "meeting", "summit", "occurrence"},
}


async def disambiguate(
    candidate: EntityCandidate,
    ollama_host: str | None = None,
) -> DisambiguationResult | None:
    """
    Disambiguate one EntityCandidate against Wikidata.
    Returns None if confidence < _LLM_THRESHOLD or if LLM is unavailable for grey zone.
    """
    raw = await wd_client.search_entities(candidate.surface_form)
    if not raw:
        log.debug("wikidata_no_candidates", surface=candidate.surface_form)
        return None

    hits = [_parse_hit(r) for r in raw]
    scored = sorted([(h, _score(candidate, h)) for h in hits], key=lambda x: x[1], reverse=True)
    best_hit, best_conf = scored[0]

    log.debug(
        "wikidata_disambig",
        surface=candidate.surface_form,
        top_qid=best_hit.qid,
        confidence=round(best_conf, 3),
    )

    if best_conf >= _AUTO_THRESHOLD:
        return DisambiguationResult(
            qid=best_hit.qid,
            label=best_hit.label,
            confidence=best_conf,
            method="auto",
            surface_form=candidate.surface_form,
        )

    if best_conf >= _LLM_THRESHOLD and ollama_host:
        return await _llm_disambig(candidate, scored[:3], ollama_host)

    return None


def _score(candidate: EntityCandidate, hit: WikidataSearchResult) -> float:
    sm = _string_match(candidate.surface_form, hit.label)
    tm = _type_match(candidate.type_hint, hit.description)
    co = _context_overlap(candidate.context_chunks, hit.description)
    return sm * 0.6 + tm * 0.25 + co * 0.15


def _string_match(surface: str, label: str) -> float:
    s, l = surface.lower().strip(), label.lower().strip()
    if s == l:
        return 1.0
    return SequenceMatcher(None, s, l).ratio()


def _type_match(type_hint: str, description: str) -> float:
    if not type_hint:
        return 0.5
    keywords = _TYPE_KEYWORDS.get(type_hint, set())
    if not keywords:
        return 0.5  # Concept — no strong signal
    desc_lower = description.lower()
    return 1.0 if any(kw in desc_lower for kw in keywords) else 0.2


def _context_overlap(context_chunks: list[str], description: str) -> float:
    if not description or not context_chunks:
        return 0.5
    desc_tokens = set(re.findall(r"\w+", description.lower()))
    ctx_tokens = set(re.findall(r"\w+", " ".join(context_chunks[:3]).lower()))
    if not desc_tokens or not ctx_tokens:
        return 0.5
    overlap = len(desc_tokens & ctx_tokens)
    return min(1.0, overlap / len(desc_tokens))  # fraction of desc tokens covered by context


def _parse_hit(raw: dict[str, Any]) -> WikidataSearchResult:
    return WikidataSearchResult(
        qid=raw.get("id", ""),
        label=raw.get("label", ""),
        description=raw.get("description", ""),
        aliases=[a.get("value", "") for a in raw.get("aliases", []) if isinstance(a, dict)],
    )


async def _llm_disambig(
    candidate: EntityCandidate,
    top3: list[tuple[WikidataSearchResult, float]],
    ollama_host: str,
) -> DisambiguationResult | None:
    options = "\n".join(
        f'{i+1}. {h.qid}: "{h.label}" — {h.description}'
        for i, (h, _) in enumerate(top3)
    )
    prompt = (
        f'Surface: "{candidate.surface_form}" | Type: {candidate.type_hint or "unknown"}\n\n'
        f"Candidates:\n{options}\n\n"
        f'Reply JSON: {{"choice": 1}} (1–{len(top3)}), or {{"choice": 0}} if none match.'
    )
    payload = {
        "model": "qwen3:8b",
        "messages": [{"role": "user", "content": prompt}],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 32},
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(f"{ollama_host}/api/chat", json=payload)
            r.raise_for_status()
        choice = json.loads(r.json().get("message", {}).get("content", "{}")).get("choice", 0)
        if choice and 1 <= int(choice) <= len(top3):
            hit, sc = top3[int(choice) - 1]
            return DisambiguationResult(
                qid=hit.qid,
                label=hit.label,
                confidence=max(float(sc), _LLM_THRESHOLD),
                method="llm",
                surface_form=candidate.surface_form,
            )
    except Exception as exc:
        log.warning("wikidata_llm_disambig_failed", error=str(exc))
    return None
