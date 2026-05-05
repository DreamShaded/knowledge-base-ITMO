"""Faithfulness check pipeline: 4-step atomic claim verification (ADR-0007, phase-09).

Step 0: atomic decomposition (atomic-decomp.py)
Step 1: batch claim ↔ quote verification (batch-claim-quote.py)
Step 2: partial → corpus check (corpus-check.py)
Step 3: none → mark ⚠ (handled by renderer; no-corpus fallback stays partial⚠)
Step 4: cross-source check for negation claims (cross-source.py, optional)
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from app.logging import get_logger
from app.services.faithfulness.schemas import ClaimVerdict, FaithfulnessResult

if TYPE_CHECKING:
    from app.services.faithfulness.cache import FaithfulnessCache
    from app.services.generator import AnswerEnvelope

log = get_logger(__name__)

RetrievalFn = Callable[[str], Awaitable[list[str]]]

_AGGREGATE_THRESHOLD = 0.5  # ≥50% claims with warning → aggregate warning
_DIR = Path(__file__).parent


def _load_mod(logical: str, filename: str):
    """Load a kebab-case sibling module and register it in sys.modules."""
    fqname = f"app.services.faithfulness.{logical}"
    if fqname not in sys.modules:
        spec = importlib.util.spec_from_file_location(fqname, _DIR / filename)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[fqname] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return sys.modules[fqname]


async def check(
    envelope: "AnswerEnvelope",
    *,
    ollama_host: str,
    model: str = "qwen3:8b",
    cache: "FaithfulnessCache | None" = None,
    retrieval_fn: RetrievalFn | None = None,
    no_check: bool = False,
    intent: str = "",
    enable_cross_source: bool = False,
    timeout: float = 120.0,
) -> FaithfulnessResult:
    """Run faithfulness check on Z1 sections of envelope.

    Returns FaithfulnessResult with per-claim verdicts and aggregate warning flag.
    Skips check on: no_check flag, intent=personal, refusal envelope, or timeout.
    timeout: match AppConfig.ollama.timeout (120 s default). PRD §7.1 3 s p95 target
    is a monitoring target, not a hard cut — callers may tighten via this param.
    """
    if no_check:
        return FaithfulnessResult(skipped=True, skip_reason="no-check")
    if intent == "personal":
        return FaithfulnessResult(skipped=True, skip_reason="intent=personal")
    if envelope.refusal:
        return FaithfulnessResult(skipped=True, skip_reason="refusal")

    z1_text = _extract_z1_text(envelope)
    if not z1_text.strip():
        return FaithfulnessResult(skipped=True, skip_reason="empty_z1")

    citation_snippets = {c.id: c.snippet for c in envelope.citations}
    canonical = _canonical(z1_text, citation_snippets)

    if cache:
        hit = await cache.get(canonical)
        if hit:
            return hit

    try:
        result = await asyncio.wait_for(
            _run_pipeline(z1_text, citation_snippets, ollama_host, model, retrieval_fn, enable_cross_source),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        log.warning("faithfulness_timeout", timeout=timeout)
        return FaithfulnessResult(skipped=True, skip_reason="timeout")

    if cache:
        await cache.set(canonical, result)

    return result


# ── internal ──────────────────────────────────────────────────────────────────

def _extract_z1_text(envelope: "AnswerEnvelope") -> str:
    return "\n\n".join(s.content for s in envelope.sections if s.zone == "Z1")


def _canonical(z1_text: str, snippets: dict[int, str]) -> str:
    snippet_str = "|".join(f"{k}:{v[:100]}" for k, v in sorted(snippets.items()))
    return z1_text + "\n---\n" + snippet_str


async def _run_pipeline(
    z1_text: str,
    citation_snippets: dict[int, str],
    ollama_host: str,
    model: str,
    retrieval_fn: RetrievalFn | None,
    enable_cross_source: bool,
) -> FaithfulnessResult:
    _decomp = _load_mod("_atomic_decomp", "atomic-decomp.py")
    _batch = _load_mod("_batch_claim_quote", "batch-claim-quote.py")
    _corpus = _load_mod("_corpus_check", "corpus-check.py")
    _cross = _load_mod("_cross_source", "cross-source.py")

    claims = await _decomp.decompose(z1_text, citation_snippets, ollama_host, model)
    if not claims:
        return FaithfulnessResult()

    verdicts: list[ClaimVerdict] = await _batch.verify_batch(claims, citation_snippets, ollama_host, model)
    verdicts = await _corpus.corpus_check(verdicts, retrieval_fn)

    if enable_cross_source:
        verdicts = await _cross.cross_source_check(verdicts, retrieval_fn)

    return _compute_result(verdicts)


def _compute_result(verdicts: list[ClaimVerdict]) -> FaithfulnessResult:
    if not verdicts:
        return FaithfulnessResult()
    n_warnings = sum(1 for v in verdicts if v.warning)
    fraction = n_warnings / len(verdicts)
    return FaithfulnessResult(
        verdicts=verdicts,
        aggregate_warning=fraction >= _AGGREGATE_THRESHOLD,
        warning_fraction=fraction,
    )
