#!/usr/bin/env python3
"""Tier-0 quality acceptance check script (PRD §11 п.2).

Connects to the embedded Oxigraph store, samples up to 100 random triples
from <urn:tier0>, and writes them to CSV for manual review.

Target: ≥80% of sampled triples correct on manual rubric.

Usage:
    uv run scripts/eval-tier0-quality.py [--sample N] [--out path/to/out.csv]
"""
from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Sample Tier-0 triples for manual QA")
    parser.add_argument("--sample", type=int, default=100, help="Number of triples to sample")
    parser.add_argument(
        "--out", default="plans/reports/tier0-quality-sample.csv",
        help="Output CSV path (relative to repo root)"
    )
    args = parser.parse_args()

    import pyoxigraph as px

    store_path = str(REPO_ROOT / "data" / "oxigraph")
    if not Path(store_path).exists():
        print(f"ERROR: Oxigraph store not found at {store_path}", file=sys.stderr)
        print("Run the indexer first to populate tier0 triples.", file=sys.stderr)
        sys.exit(1)

    store = px.Store(store_path)

    # Count total triples
    total_result = list(store.query(
        "SELECT (COUNT(*) AS ?n) WHERE { GRAPH <urn:tier0> { ?s ?p ?o } }"
    ))
    total = int(total_result[0]["n"].value) if total_result else 0
    print(f"Total triples in <urn:tier0>: {total:,}")

    if total == 0:
        print("No triples found. Nothing to evaluate.", file=sys.stderr)
        sys.exit(0)

    # Fetch all triples (up to 50k to keep memory reasonable)
    fetch_limit = min(50_000, total)
    solutions = store.query(
        f"SELECT ?s ?p ?o WHERE {{ GRAPH <urn:tier0> {{ ?s ?p ?o }} }} LIMIT {fetch_limit}"
    )
    var_names = [v.value for v in solutions.variables]
    all_rows = [
        {var: (sol[i].value if sol[i] is not None else "") for i, var in enumerate(var_names)}
        for sol in solutions
    ]

    sample_size = min(args.sample, len(all_rows))
    sampled = random.sample(all_rows, sample_size)

    out_path = REPO_ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["s", "p", "o", "correct", "notes"],
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in sampled:
            row["correct"] = ""   # reviewer fills: 1=correct, 0=incorrect
            row["notes"] = ""
            writer.writerow(row)

    print(f"Sampled {sample_size}/{total:,} triples → {out_path}")
    print()
    print("Manual review rubric:")
    print("  1 = triple is correct (subject, predicate, object all make sense)")
    print("  0 = incorrect (wrong URI, wrong predicate, garbage value)")
    print()
    print(f"PRD §11 п.2 target: ≥80% correct on sample of {sample_size}")

    # Quick automated stats
    _print_stats(store)


def _print_stats(store) -> None:
    queries = {
        "Total triples": "SELECT (COUNT(*) AS ?n) WHERE { GRAPH <urn:tier0> { ?s ?p ?o } }",
        "Notes indexed": "SELECT (COUNT(DISTINCT ?n) AS ?n) WHERE { GRAPH <urn:tier0> { ?n a <https://my-pkm.local/ontology#Note> } }",
        "Unique tags": "SELECT (COUNT(DISTINCT ?t) AS ?n) WHERE { GRAPH <urn:tier0> { ?n <https://my-pkm.local/ontology#tagged> ?t } }",
        "Wiki-links": "SELECT (COUNT(*) AS ?n) WHERE { GRAPH <urn:tier0> { ?s <https://my-pkm.local/ontology#linksTo> ?o } }",
        "Headings": "SELECT (COUNT(*) AS ?n) WHERE { GRAPH <urn:tier0> { ?s <https://my-pkm.local/ontology#hasHeading> ?h } }",
        "Books": "SELECT (COUNT(DISTINCT ?b) AS ?n) WHERE { GRAPH <urn:tier0> { ?b a <https://my-pkm.local/ontology#Book> } }",
    }
    print("\n--- Tier-0 stats ---")
    for label, q in queries.items():
        try:
            result = list(store.query(q))
            n = result[0]["n"].value if result else "0"
            print(f"  {label:<20}: {int(n):>8,}")
        except Exception as e:
            print(f"  {label:<20}: ERROR ({e})")


if __name__ == "__main__":
    main()
