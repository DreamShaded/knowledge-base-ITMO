"""Step 1: Batch claim ↔ quote verification in a single LLM call (ADR-0007)."""
from __future__ import annotations

import json

import httpx

from app.logging import get_logger
from app.services.faithfulness.schemas import AtomicClaim, ClaimVerdict

log = get_logger(__name__)

_SYSTEM = """\
You are a fact-checker. Verify each claim against its cited quotes.

verdict values:
- full: claim fully supported by the quote(s)
- partial: claim partially supported, key details missing
- none: claim not supported by any quote (or no quotes provided)

Respond with JSON only:
{"results": [{"claim_index": 0, "verdict": "full|partial|none", "reasoning_short": "..."}]}"""


async def verify_batch(
    claims: list[AtomicClaim],
    citation_snippets: dict[int, str],
    ollama_host: str,
    model: str = "qwen3:8b",
    timeout: float = 20.0,
) -> list[ClaimVerdict]:
    """Verify each claim against its cited quotes in one LLM call."""
    if not claims:
        return []

    user_msg = _build_pairs_block(claims, citation_snippets)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 512},
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{ollama_host}/api/chat", json=payload)
            r.raise_for_status()
            content = r.json().get("message", {}).get("content", "{}")
        results = json.loads(content).get("results", [])
        verdicts = merge_verdicts(claims, results)
        log.debug("batch_verify_done", claims=len(claims), warnings=sum(v.warning for v in verdicts))
        return verdicts
    except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
        log.warning("batch_verify_llm_error", error=str(e))
        return fallback_verdicts(claims)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.warning("batch_verify_parse_error", error=str(e))
        return fallback_verdicts(claims)


def _build_pairs_block(claims: list[AtomicClaim], snippets: dict[int, str]) -> str:
    lines = ["Verify each numbered claim against its cited quotes:\n"]
    for i, claim in enumerate(claims):
        lines.append(f"Claim {i}: {claim.text}")
        if claim.citation_refs:
            for ref in claim.citation_refs:
                snippet = snippets.get(ref, "")
                if snippet:
                    lines.append(f"  Quote [^{ref}]: {snippet[:300]}")
        else:
            lines.append("  (no citations provided)")
        lines.append("")
    return "\n".join(lines)


def merge_verdicts(claims: list[AtomicClaim], results: list[dict]) -> list[ClaimVerdict]:
    """Merge LLM results back onto claims. Exposed for testing."""
    idx_map = {r.get("claim_index", -1): r for r in results if isinstance(r, dict)}
    out: list[ClaimVerdict] = []
    for i, claim in enumerate(claims):
        r = idx_map.get(i, {})
        verdict_str = r.get("verdict", "none")
        if verdict_str not in ("full", "partial", "none"):
            verdict_str = "none"
        out.append(ClaimVerdict(
            claim=claim,
            verdict=verdict_str,  # type: ignore[arg-type]
            warning=verdict_str in ("partial", "none"),
            reasoning_short=str(r.get("reasoning_short", "")),
        ))
    return out


def fallback_verdicts(claims: list[AtomicClaim]) -> list[ClaimVerdict]:
    """All claims get none/warning on LLM error. Exposed for testing."""
    return [
        ClaimVerdict(claim=c, verdict="none", warning=True, reasoning_short="llm_error")
        for c in claims
    ]
