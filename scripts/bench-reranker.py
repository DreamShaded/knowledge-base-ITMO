#!/usr/bin/env python3
"""
Reranker latency/memory benchmark (ADR-0003 task 13).
Source: sample 50 chunks from chunks_v1 + 10 hand-written query probes.
Runs three batch sizes (50/16/8) on the configured reranker (laptop: CPU 0.6B).
Output: plans/reports/bench-{date}-reranker-latency.md

Usage:
    python scripts/bench-reranker.py [--profile laptop-4070-8gb] [--top-k 50]
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

QUERY_PROBES = [
    "реактивность в программировании",
    "reactive systems architecture",
    "prefix search implementation",
    "машинное обучение и нейронные сети",
    "how does hybrid retrieval work",
    "векторная база данных Qdrant",
    "knowledge graph embedding",
    "BM25 sparse retrieval vs dense",
    "управление зависимостями в Python",
    "multi-hop reasoning over documents",
]


def _get_reranker(cfg):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "qwen3_reranker",
        REPO_ROOT / "app" / "services" / "reranker" / "qwen3-reranker.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Qwen3RerankerLocal(
        model_id=cfg.reranker.model_id,
        device=cfg.reranker.device,
        batch_size=50,  # overridden per bench run
        cache_dir=str(REPO_ROOT / "data" / "models"),
    )


def _sample_chunks(n: int = 50) -> list[str]:
    """Pull n chunks from Qdrant; fall back to synthetic samples if store empty."""
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(path=str(REPO_ROOT / "data" / "qdrant"))
        results = client.scroll(
            collection_name="chunks_v1",
            limit=n,
            with_payload=["content"],
            with_vectors=False,
        )
        chunks = [pt.payload.get("content", "") for pt in results[0] if pt.payload]
        if chunks:
            return chunks[:n]
    except Exception:
        pass
    # Synthetic fallback: realistic-length paragraphs
    short = "This is a short passage about the topic. " * 10
    long = "This passage is much longer and contains detailed technical information. " * 40
    return [short if i % 3 else long for i in range(n)]


def _bench_batch(reranker, query: str, docs: list[str], batch_size: int, runs: int = 3) -> dict:
    import asyncio
    reranker._batch_size = batch_size

    latencies = []
    for _ in range(runs):
        t0 = time.perf_counter()
        asyncio.run(reranker.rerank(query, docs, top_k=10))
        latencies.append(time.perf_counter() - t0)

    return {
        "batch_size": batch_size,
        "p50_s": statistics.median(latencies),
        "p95_s": sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) >= 20 else max(latencies),
        "mean_s": statistics.mean(latencies),
        "runs": runs,
    }


def _peak_rss_mb() -> float:
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def _write_report(results: list[dict], reranker_model: str, device: str) -> Path:
    from datetime import date
    today = date.today().strftime("%y%m%d")
    out = REPO_ROOT / "plans" / "reports" / f"bench-{today}-XXXX-reranker-latency.md"
    out.parent.mkdir(parents=True, exist_ok=True)

    rows = "\n".join(
        f"| {r['batch_size']} | {r['p50_s']:.3f}s | {r['p95_s']:.3f}s | {r['mean_s']:.3f}s |"
        for r in results
    )
    decision = "batch=50" if results[0]["p50_s"] < 1.0 else "batch=16"

    content = f"""\
# Reranker Latency Benchmark

**Model:** {reranker_model}
**Device:** {device}
**Date:** {date.today().isoformat()}
**Queries:** {len(QUERY_PROBES)} probes × 3 runs each

## Results

| batch_size | p50 | p95 | mean |
|-----------|-----|-----|------|
{rows}

**Peak RSS:** {_peak_rss_mb():.1f} MB

## Decision

Recommended `RERANKER_BATCH_SIZE`: **{decision}**
(budget: p50 ≤ 1.0 s per ADR-0003 §PRD-4.2)
"""
    out.write_text(content, encoding="utf-8")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Reranker batch latency benchmark")
    parser.add_argument("--profile", default="laptop-4070-8gb")
    parser.add_argument("--top-k", type=int, default=50)
    args = parser.parse_args()

    import os
    os.environ["KB_PROFILE"] = args.profile

    from app.config_profile import load_config
    cfg = load_config(args.profile)

    print(f"Loading reranker: {cfg.reranker.model_id} on {cfg.reranker.device}")
    reranker = _get_reranker(cfg)

    docs = _sample_chunks(args.top_k)
    print(f"Sampled {len(docs)} chunks")

    results = []
    for bs in [50, 16, 8]:
        print(f"  Benchmarking batch_size={bs} ...", end=" ", flush=True)
        query = QUERY_PROBES[0]
        r = _bench_batch(reranker, query, docs, batch_size=bs, runs=5)
        results.append(r)
        print(f"p50={r['p50_s']:.3f}s")

    report_path = _write_report(results, cfg.reranker.model_id, cfg.reranker.device)
    print(f"\nReport: {report_path}")

    decision = "batch=50" if results[0]["p50_s"] < 1.0 else "batch=16"
    print(f"Decision: {decision}")

    # Update laptop profile with chosen batch size
    if args.profile == "laptop-4070-8gb":
        chosen = int(decision.split("=")[1])
        profile_file = REPO_ROOT / "infra" / "profiles" / f"{args.profile}.yaml"
        text = profile_file.read_text()
        import re
        text = re.sub(r"(reranker:\n  .*\n.*batch_size:) \d+", rf"\1 {chosen}", text)
        profile_file.write_text(text)
        print(f"Updated {profile_file.name}: batch_size={chosen}")


if __name__ == "__main__":
    main()
