"""Step 0: Atomic decomposition of Z1 section into verifiable claims (ADR-0007)."""
from __future__ import annotations

import functools
import json
from pathlib import Path

import httpx

from app.logging import get_logger
from app.services.faithfulness.schemas import AtomicClaim

log = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "infra" / "prompts"


@functools.lru_cache(maxsize=1)
def _load_prompt() -> str:
    path = _PROMPTS_DIR / "atomic_decomposition.md"
    return path.read_text(encoding="utf-8") if path.exists() else _FALLBACK_PROMPT


async def decompose(
    z1_text: str,
    citation_snippets: dict[int, str],
    ollama_host: str,
    model: str = "qwen3:8b",
    timeout: float = 20.0,
) -> list[AtomicClaim]:
    """Decompose Z1 section text into atomic claims via LLM."""
    if not z1_text.strip():
        return []

    citations_block = "\n".join(
        f"[^{ref}]: {snippet[:200]}"
        for ref, snippet in sorted(citation_snippets.items())
    )
    system_msg = _load_prompt().replace("{z1_text}", z1_text).replace("{citations}", citations_block)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": "Decompose the section into atomic claims."},
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
        claims_raw = json.loads(content).get("claims", [])
        claims = [_parse_claim(c) for c in claims_raw if isinstance(c, dict)]
        log.debug("atomic_decomp_done", count=len(claims))
        return claims
    except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
        log.warning("atomic_decomp_llm_error", error=str(e))
        return []
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.warning("atomic_decomp_parse_error", error=str(e))
        return []


def parse_claims_json(raw_json: str) -> list[AtomicClaim]:
    """Parse LLM JSON output into AtomicClaim list. Exposed for testing."""
    try:
        data = json.loads(raw_json)
        return [_parse_claim(c) for c in data.get("claims", []) if isinstance(c, dict)]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


def _parse_claim(raw: dict) -> AtomicClaim:
    valid_types = {"factual_specific", "factual_general", "opinion", "framing", "negation"}
    claim_type = raw.get("claim_type", "factual_general")
    if claim_type not in valid_types:
        claim_type = "factual_general"
    refs = raw.get("citation_refs", [])
    if not isinstance(refs, list):
        refs = []
    refs = [r for r in refs if isinstance(r, int)]
    return AtomicClaim(
        text=str(raw.get("text", "")),
        citation_refs=refs,
        claim_type=claim_type,  # type: ignore[arg-type]
    )


_FALLBACK_PROMPT = """\
You are a fact-checker. Decompose the Z1 section into atomic, verifiable claims.

Z1 Section:
{z1_text}

Citations:
{citations}

Respond with JSON only:
{"claims": [{"text": "...", "citation_refs": [1], "claim_type": "factual_specific"}]}

claim_type: factual_specific | factual_general | opinion | framing | negation
citation_refs: list of [^N] numbers that the claim cites (empty list if none)."""
