"""
Eval runner — executes gold questions / sentinels against the app endpoint
and saves per-question JSON results to evals/runs/<ts>/ (phase-14 task 4).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.logging import get_logger

log = get_logger(__name__)

_RUNS_DIR = Path("evals") / "runs"


def load_gold(gold_dir: Path, category: str | None = None) -> list[dict]:
    """Load q-*.yaml files from gold_dir, optionally filtered by category."""
    questions = []
    for f in sorted(gold_dir.glob("q-*.yaml")):
        try:
            q = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("gold_load_error", file=str(f), error=str(exc))
            continue
        if category and q.get("category") != category:
            continue
        questions.append(q)
    return questions


def load_sentinels(sentinels_dir: Path) -> list[dict]:
    """Load S*.yaml sentinel definitions."""
    sentinels = []
    for f in sorted(sentinels_dir.glob("S*.yaml")):
        try:
            s = yaml.safe_load(f.read_text(encoding="utf-8"))
            sentinels.append(s)
        except Exception as exc:
            log.warning("sentinel_load_error", file=str(f), error=str(exc))
    return sentinels


def run_eval(
    questions: list[dict],
    endpoint_url: str,
    run_dir: Path,
) -> list[dict]:
    """
    Execute eval for each question, save individual JSON results.
    Returns list of result dicts.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for q in questions:
        result = _run_one(q, endpoint_url)
        out_path = run_dir / f"{q['id']}.json"
        out_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        results.append(result)
        status = result.get("status", "?")
        log.info("eval_question_done", id=q["id"], status=status,
                 latency_ms=result.get("latency_ms"))
    return results


def make_run_dir(base: Path | None = None) -> Path:
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = (base or _RUNS_DIR) / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


# ── internal ───────────────────────────────────────────────────────────────────

def _run_one(q: dict, endpoint_url: str) -> dict:
    try:
        import httpx
    except ImportError:
        import urllib.request, urllib.error  # fallback for environments without httpx
        return _run_one_urllib(q, endpoint_url)

    start = time.perf_counter()
    try:
        resp = httpx.post(
            endpoint_url,
            json={"model": "default", "messages": [{"role": "user", "content": q["query"]}]},
            timeout=60.0,
        )
        resp.raise_for_status()
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        body = resp.json()
        answer = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {
            "id": q["id"],
            "category": q.get("category"),
            "query": q["query"],
            "answer": answer,
            "envelope": body.get("envelope"),
            "latency_ms": elapsed_ms,
            "status": "ok",
        }
    except Exception as exc:
        return {
            "id": q["id"],
            "category": q.get("category"),
            "query": q["query"],
            "answer": "",
            "envelope": None,
            "latency_ms": -1,
            "status": "error",
            "error": str(exc),
        }


def _run_one_urllib(q: dict, endpoint_url: str) -> dict:
    import json as _json
    import urllib.request
    start = time.perf_counter()
    try:
        payload = _json.dumps(
            {"model": "default", "messages": [{"role": "user", "content": q["query"]}]}
        ).encode()
        req = urllib.request.Request(
            endpoint_url, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = _json.loads(resp.read().decode())
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        answer = body.get("choices", [{}])[0].get("message", {}).get("content", "")
        return {"id": q["id"], "category": q.get("category"), "query": q["query"],
                "answer": answer, "envelope": body.get("envelope"),
                "latency_ms": elapsed_ms, "status": "ok"}
    except Exception as exc:
        return {"id": q["id"], "category": q.get("category"), "query": q["query"],
                "answer": "", "envelope": None, "latency_ms": -1,
                "status": "error", "error": str(exc)}
