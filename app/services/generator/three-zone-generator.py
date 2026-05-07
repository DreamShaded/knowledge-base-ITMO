"""Three-zone generator using Ollama JSON format (ADR-0004 §4).

Z1 — grounded core: every factual claim cites [^N] from retrieved context.
Z2 — parametric framing: bridging context without citations.
Z3 — explicit fallback "Из общих знаний": only when retrieval insufficient;
     suppressed when strict_mode=True (/strict command).
"""
from __future__ import annotations

import functools
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from app.logging import get_logger, get_llm_logger

if TYPE_CHECKING:
    from app.services.generator import AnswerEnvelope
    from app.services.retrieval.schemas import RetrievalResult

log = get_logger(__name__)
llm_log = get_llm_logger()

_PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "infra" / "prompts"
_PROMPT_VERSION = "three_zone_generator_v2"

_SOURCE_TYPE_LABEL = {
    "note": "Заметка",
    "book_chapter": "Книга",
    "web_saved": "Сохранённая страница",
    "web": "Веб",
    "wikidata": "Wikidata",
}
_MAX_CONTEXT_CHARS = 24_000  # ~6K tokens; conservative budget for qwen3:8b 32K window


@functools.lru_cache(maxsize=1)
def _load_prompt() -> str:
    path = _PROMPTS_DIR / "three_zone_generator.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return _FALLBACK_PROMPT


def _build_context_block(result: "RetrievalResult", strict_mode: bool) -> tuple[str, list[dict]]:
    """Format retrieved parents into a numbered context block.

    Returns (context_text, citations_list).
    """
    lines: list[str] = []
    citations: list[dict] = []
    char_budget = _MAX_CONTEXT_CHARS

    for idx, parent in enumerate(result.parents, start=1):
        meta = parent.source_meta
        # Derive readable title from source_id (strip vault prefix + extension)
        sid = meta.source_id
        rel = sid.split(":", 1)[1] if ":" in sid else sid
        title = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0] if rel else parent.parent_id
        type_label = _SOURCE_TYPE_LABEL.get(meta.source_type, meta.source_type)
        vault_hint = f" · {meta.vault_id}" if meta.vault_id and meta.vault_id != "_web_saved" else ""
        header = f"[^{idx}] [{type_label}] **{title}**{vault_hint}"
        snippet = parent.content[:300].replace("\n", " ")

        entry = f"{header}\n{parent.content}"
        if len(entry) > char_budget:
            entry = f"{header}\n{parent.content[:char_budget]}\n[truncated]"
        char_budget -= len(entry)

        lines.append(entry)
        citations.append({
            "id": idx,
            "source_id": meta.source_id,
            "source_type": meta.source_type,
            "vault_id": meta.vault_id,
            "title": title,
            "snippet": snippet,
        })

        if char_budget <= 0:
            log.debug("context_budget_exhausted", parents_total=len(result.parents), used=idx)
            break

    return "\n\n---\n\n".join(lines), citations


def _build_system_prompt(
    prompt_template: str,
    query: str,
    context_block: str,
    strict_mode: bool,
    retrieval_hit_count: int,
) -> str:
    z3_instruction = (
        "DO NOT include a Z3 section (strict mode active)."
        if strict_mode
        else (
            'Include a Z3 "Из общих знаний" section ONLY if retrieved context is insufficient '
            "for a complete answer."
        )
    )
    return (
        prompt_template
        .replace("{query}", query)
        .replace("{context}", context_block)
        .replace("{z3_instruction}", z3_instruction)
        .replace("{retrieval_hit_count}", str(retrieval_hit_count))
    )


async def generate(
    query: str,
    result: "RetrievalResult",
    ollama_host: str,
    model: str = "qwen3:8b",
    strict_mode: bool = False,
    timeout: float = 300.0,
) -> "AnswerEnvelope":
    """Generate a three-zone AnswerEnvelope from retrieval result."""
    from app.services.generator import AnswerEnvelope, Section, Citation, ConfidenceSignal

    context_block, citation_defs = _build_context_block(result, strict_mode)
    hit_count = len(result.parents)

    prompt_template = _load_prompt()
    system_msg = _build_system_prompt(
        prompt_template, query, context_block, strict_mode, hit_count
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": query},
        ],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 2048},
    }

    try:
        _t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{ollama_host}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
        _latency_ms = int((time.monotonic() - _t0) * 1000)

        import app.logging as _logging_mod
        if _logging_mod.log_llm_full:
            llm_log.info(
                "llm_call",
                provider="ollama",
                model=model,
                prompt_version=_PROMPT_VERSION,
                latency_ms=_latency_ms,
                prompt=payload["messages"][0]["content"][:500],
            )
        else:
            llm_log.info(
                "llm_call",
                provider="ollama",
                model=model,
                prompt_version=_PROMPT_VERSION,
                latency_ms=_latency_ms,
            )

        content = data.get("message", {}).get("content", "{}")
        parsed = json.loads(content)

        sections = [Section(**s) for s in parsed.get("sections", [])]
        if strict_mode:
            sections = [s for s in sections if s.zone != "Z3"]

        # Merge LLM citations with authoritative citation_defs:
        # LLM can adjust title/snippet but source metadata must come from citation_defs.
        cdef_by_id = {c["id"]: c for c in citation_defs}
        raw_citations = parsed.get("citations", [])
        merged: list[dict] = []
        for c in raw_citations:
            base = dict(cdef_by_id.get(c.get("id"), c))
            for field in ("title", "snippet"):
                if c.get(field):
                    base[field] = c[field]
            merged.append(base)
        citations = [Citation(**c) for c in (merged or citation_defs)]

        confidence_raw = parsed.get("confidence_signal", {})
        confidence = ConfidenceSignal(
            level=confidence_raw.get("level", "medium"),
            reason=confidence_raw.get("reason", ""),
            retrieval_hit_count=hit_count,
        )

        envelope = AnswerEnvelope(
            tldr=parsed.get("tldr", ""),
            sections=sections,
            related=parsed.get("related", []),
            citations=citations,
            confidence_signal=confidence,
            prompt_version=_PROMPT_VERSION,
        )
        log.info(
            "generation_done",
            query=query[:60],
            zones=[s.zone for s in sections],
            citations=len(citations),
            confidence=confidence.level,
        )
        return envelope

    except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
        log.error("generation_llm_error", error=str(e), query=query[:60])
        raise
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.error("generation_parse_error", error=str(e), query=query[:60])
        raise


def make_refusal_envelope(hint: str = "") -> "AnswerEnvelope":
    """Return a fixed refusal envelope (no hallucination)."""
    from app.services.generator import AnswerEnvelope, ConfidenceSignal

    msg = "Не нашёл в источниках."
    if hint:
        msg += f" Попробуйте переформулировать или использовать `/explain`."

    return AnswerEnvelope(
        tldr=msg,
        refusal=True,
        refusal_message=msg,
        confidence_signal=ConfidenceSignal(level="low", reason="empty_retrieval"),
        prompt_version=_PROMPT_VERSION,
    )


# Fallback prompt used if infra/prompts/three_zone_generator.md is missing
_FALLBACK_PROMPT = """\
You are a knowledge base assistant. Answer the query using ONLY the provided context.
Generate a structured JSON response with three zones.

Context:
{context}

Query: {query}

{z3_instruction}

Respond with JSON only:
{
  "tldr": "one-sentence summary",
  "sections": [
    {"zone": "Z1", "heading": "Ответ", "content": "...facts with [^N] citations..."},
    {"zone": "Z2", "heading": "Контекст", "content": "...background without citations..."}
  ],
  "related": ["topic1", "topic2"],
  "citations": [
    {"id": 1, "source_id": "...", "source_type": "note", "vault_id": "...", "title": "...", "snippet": "..."}
  ],
  "confidence_signal": {"level": "high", "reason": "...", "retrieval_hit_count": {retrieval_hit_count}}
}"""
