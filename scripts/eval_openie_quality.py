#!/usr/bin/env python3
"""
Manual rubric eval for Tier-1 OpenIE quality (ADR-0008 §3, phase-10).

Samples N triples from <urn:tier1>, writes a CSV for human judgment,
then optionally reads a completed CSV and computes accuracy.

Usage:
  # Generate eval set (requires running app with Oxigraph store)
  python scripts/eval_openie_quality.py sample --n 500 --out data/eval_openie.csv

  # Score a completed judgment CSV
  python scripts/eval_openie_quality.py score --csv data/eval_openie.csv

  # Full round-trip (sample + open in editor + score)
  python scripts/eval_openie_quality.py sample --n 100 --open
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

_TIER1_SAMPLE_SPARQL = """
PREFIX pkm: <https://my-pkm.local/ontology#>
SELECT ?s ?p ?o ?conf ?chunk ?doc WHERE {{
  GRAPH <urn:tier1> {{
    ?s ?p ?o .
    OPTIONAL {{ ?s ?p ?o ; pkm:confidence ?conf }}
    OPTIONAL {{ ?s ?p ?o ; pkm:evidenceChunk ?chunk }}
    OPTIONAL {{ ?s ?p ?o ; pkm:evidenceDoc ?doc }}
  }}
  FILTER NOT EXISTS {{ FILTER(?p IN (
      pkm:provenance, pkm:confidence, pkm:source,
      pkm:evidenceChunk, pkm:evidenceDoc, pkm:conflictFlag
  )) }}
}}
LIMIT {limit}
"""

_CSV_COLUMNS = [
    "triple",
    "subject",
    "predicate",
    "object",
    "confidence",
    "evidence_chunk",
    "evidence_doc",
    "judgment_y_n",  # Y = correct, N = incorrect — filled by human
    "reason",        # optional free-text explanation
]

_TARGET_ACCURACY = 0.75  # ADR-0008 §3 calibration target


def cmd_sample(args: argparse.Namespace) -> None:
    from app.config_profile import load_config
    from app.di.composition import build_container

    cfg = load_config()
    container = build_container(cfg)
    container.oxigraph.init()

    limit = max(args.n * 5, 2000)  # over-fetch, then random sample
    sparql = _TIER1_SAMPLE_SPARQL.format(limit=limit)
    rows = container.oxigraph.sparql_select(sparql)

    # Deduplicate by (s, p, o)
    seen: set[tuple[str, str, str]] = set()
    unique = []
    for row in rows:
        key = (row.get("s", ""), row.get("p", ""), row.get("o", ""))
        if key not in seen:
            seen.add(key)
            unique.append(row)

    if len(unique) < args.n:
        print(f"[warn] only {len(unique)} unique triples in <urn:tier1>, requested {args.n}")

    sample = random.sample(unique, min(args.n, len(unique)))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for row in sample:
            s = row.get("s", "")
            p = row.get("p", "")
            o = row.get("o", "")
            writer.writerow({
                "triple": f"<{s}> <{p}> <{o}>",
                "subject": s,
                "predicate": p,
                "object": o,
                "confidence": row.get("conf", ""),
                "evidence_chunk": row.get("chunk", ""),
                "evidence_doc": row.get("doc", ""),
                "judgment_y_n": "",
                "reason": "",
            })

    print(f"Wrote {len(sample)} triples → {out_path}")
    print(f"Fill in 'judgment_y_n' (Y/N) and optionally 'reason' for each row.")
    print(f"Target accuracy: ≥{_TARGET_ACCURACY:.0%} (ADR-0008 §3)")

    if getattr(args, "open", False):
        editor = os.environ.get("EDITOR", "nano")
        subprocess.run([editor, str(out_path)])
        cmd_score(args)


def cmd_score(args: argparse.Namespace) -> None:
    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"[error] file not found: {csv_path}")
        sys.exit(1)

    total = 0
    correct = 0
    unanswered = 0

    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            j = row.get("judgment_y_n", "").strip().upper()
            if j not in ("Y", "N"):
                unanswered += 1
                continue
            total += 1
            if j == "Y":
                correct += 1

    if total == 0:
        print("[error] no completed judgments found (fill in Y/N in judgment_y_n column)")
        sys.exit(1)

    accuracy = correct / total
    passed = accuracy >= _TARGET_ACCURACY

    print(f"\n{'─'*40}")
    print(f"Evaluated:  {total} triples")
    print(f"Correct:    {correct} ({accuracy:.1%})")
    print(f"Incorrect:  {total - correct}")
    if unanswered:
        print(f"Unanswered: {unanswered} (skipped)")
    print(f"Target:     ≥{_TARGET_ACCURACY:.0%}")
    print(f"Result:     {'✓ PASS' if passed else '✗ FAIL'}")
    print(f"{'─'*40}\n")

    if not passed:
        gap = _TARGET_ACCURACY - accuracy
        print(f"  Quality below target by {gap:.1%}.")
        print("  Consider: tighten confidence thresholds, improve prompt, expand whitelist.")
    sys.exit(0 if passed else 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenIE quality eval (phase-10)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sample = sub.add_parser("sample", help="Sample triples and write eval CSV")
    p_sample.add_argument("--n", type=int, default=500, help="Number of triples to sample")
    p_sample.add_argument("--out", default="data/eval_openie.csv", help="Output CSV path")
    p_sample.add_argument("--open", action="store_true", help="Open CSV in $EDITOR after writing")
    p_sample.set_defaults(func=cmd_sample)

    p_score = sub.add_parser("score", help="Score a completed judgment CSV")
    p_score.add_argument("--csv", default="data/eval_openie.csv", help="Judgment CSV path")
    p_score.set_defaults(func=cmd_score)

    args = parser.parse_args()
    # Make --csv available in cmd_sample for --open round-trip
    if not hasattr(args, "csv"):
        args.csv = getattr(args, "out", "data/eval_openie.csv")
    args.func(args)


if __name__ == "__main__":
    main()
