"""
LLM-judge integration (ADR-0010 §5, phase-14 tasks 5-6).
Vendor-agnostic: uses OpenAI-compatible endpoint.

Required env vars:
  EVAL_JUDGE_BASE_URL  — default: Anthropic API
  EVAL_JUDGE_API_KEY   — your key
  EVAL_JUDGE_MODEL     — default: claude-sonnet-4-6
"""
from __future__ import annotations

import os
from pathlib import Path

from app.logging import get_logger

log = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent.parent.parent / "evals" / "prompts"
_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_BASE_URL = "https://api.anthropic.com/v1"


def judge_coverage(query: str, answer: str, expected_facts: list[str]) -> dict:
    """Check what fraction of expected_facts are covered in the answer."""
    prompt = _load_prompt("judge_coverage")
    facts_str = "\n".join(f"- {f}" for f in expected_facts)
    msg = f"{prompt}\n\nQuery: {query}\nAnswer: {answer}\nExpected facts:\n{facts_str}"
    return _call(msg, "coverage")


def judge_hallucination(query: str, answer: str, sources: list[str]) -> dict:
    """Detect whether the answer contains claims not supported by sources."""
    prompt = _load_prompt("judge_hallucination")
    sources_str = "\n".join(f"- {s}" for s in sources[:10])
    msg = f"{prompt}\n\nQuery: {query}\nAnswer: {answer}\nSources:\n{sources_str}"
    return _call(msg, "hallucination")


def judge_relevance(query: str, answer: str) -> dict:
    """Rate answer relevance on a 1-5 Likert scale."""
    prompt = _load_prompt("judge_relevance")
    msg = f"{prompt}\n\nQuery: {query}\nAnswer: {answer}"
    return _call(msg, "relevance")


# ── internal ───────────────────────────────────────────────────────────────────

def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.md"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    log.warning("judge_prompt_missing", name=name)
    return f"Evaluate the following for {name}. Respond with a brief JSON assessment."


def _client():
    try:
        import openai
    except ImportError as exc:
        raise ImportError("openai package required for LLM-judge: pip install openai") from exc
    base_url = os.environ.get("EVAL_JUDGE_BASE_URL", _DEFAULT_BASE_URL)
    api_key = os.environ.get("EVAL_JUDGE_API_KEY", "")
    return openai.OpenAI(base_url=base_url, api_key=api_key)


def _model() -> str:
    return os.environ.get("EVAL_JUDGE_MODEL", _DEFAULT_MODEL)


def _call(user_msg: str, judge_type: str) -> dict:
    try:
        client = _client()
        resp = client.chat.completions.create(
            model=_model(),
            messages=[{"role": "user", "content": user_msg}],
            max_tokens=512,
            temperature=0.0,
        )
        text = resp.choices[0].message.content or ""
        log.info("judge_call_ok", judge_type=judge_type, chars=len(text))
        return {"type": judge_type, "response": text, "status": "ok"}
    except Exception as exc:
        log.error("judge_call_failed", judge_type=judge_type, error=str(exc))
        return {"type": judge_type, "response": "", "status": "error", "error": str(exc)}
