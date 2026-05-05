"""LLM router fallback using Ollama JSON format (ADR-0004 §2).

Calls qwen3:8b with format=json to get RouterDecision.
Timeout: 3s. Returns None on failure — caller falls back to rule-based decision.
"""
from __future__ import annotations

import json
import time

import httpx

from app.logging import get_logger, get_llm_logger

log = get_logger(__name__)
llm_log = get_llm_logger()

_SYSTEM_PROMPT = """\
You are a query router for a personal knowledge base RAG system.
Classify the user's query and determine retrieval channel weights.

Respond with a single JSON object (no markdown, no explanation):
{
  "intent": "<lookup|multi_hop|personal|educational|fresh|comparison|refusal>",
  "channel_weights": {
    "notes": <0.0-1.0>,
    "books": <0.0-1.0>,
    "wikidata": <0.0-1.0>,
    "web": <0.0-1.0>
  },
  "entities": ["named entity 1", "named entity 2"],
  "reformulations": ["alternative phrasing 1"],
  "web_safe_query": "<safe search string if web channel needed>",
  "refusal": false
}

Channel guidelines:
- notes: personal Obsidian vault (highest weight for personal/multi-hop queries)
- books: PDF library (high weight for educational/comparison queries)
- wikidata: factual entity lookup (high for lookup queries)
- web: live web (only when explicitly needed or fresh intent)

Set refusal=true for harmful, nonsensical, or out-of-scope requests."""


async def route_with_llm(
    query: str,
    ollama_host: str,
    model: str = "qwen3:8b",
    timeout: float = 3.0,
) -> "RouterDecision | None":  # type: ignore[name-defined]  # noqa: F821
    """Call LLM router with structured JSON output. Returns None on failure."""
    from app.services.router import RouterDecision

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 512},
    }
    try:
        _t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{ollama_host}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
        _latency_ms = int((time.monotonic() - _t0) * 1000)

        content = data.get("message", {}).get("content", "{}")
        parsed = json.loads(content)
        decision = RouterDecision(**parsed)
        llm_log.info(
            "llm_call",
            provider="ollama",
            model=model,
            prompt_version="llm_router_v1",
            latency_ms=_latency_ms,
            intent=decision.intent,
        )
        return decision

    except httpx.TimeoutException:
        log.warning("llm_router_timeout", query=query[:60], timeout=timeout)
        return None
    except (httpx.HTTPStatusError, json.JSONDecodeError) as e:
        log.warning("llm_router_parse_error", error=str(e), query=query[:60])
        return None
    except Exception as e:
        log.warning("llm_router_error", error=str(e), query=query[:60])
        return None
