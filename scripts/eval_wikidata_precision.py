#!/usr/bin/env python3
"""
Wikidata disambiguation precision check (phase-11, PRD §11 п.3).
Samples N links from <urn:tier2>, writes CSV for human judgment, then scores.
Target: ≥90% correct on 50-link sample.

Usage:
  python scripts/eval_wikidata_precision.py sample --n 50 --out data/eval_wikidata_precision.csv
  python scripts/eval_wikidata_precision.py score  --csv data/eval_wikidata_precision.csv
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

_TARGET = 0.90
_SAMPLE_SPARQL = """
PREFIX pkm: <https://my-pkm.local/ontology#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?entity ?qid ?label ?level WHERE {{
  GRAPH <urn:tier2> {{
    ?entity pkm:wikidataQID ?qid ;
            pkm:enrichmentLevel ?level .
    OPTIONAL {{ ?entity rdfs:label ?label . FILTER(LANG(?label) = "en") }}
  }}
}} LIMIT {limit}
"""

_CSV_COLUMNS = [
    "entity_uri",
    "wikidata_qid",
    "wikidata_label",
    "enrichment_level",
    "wikidata_url",
    "correct_disambiguation",  # Y = correct, N = incorrect — filled by human
    "notes",
]


def cmd_sample(args: argparse.Namespace) -> None:
    from app.config_profile import load_config
    from app.di.composition import build_container

    cfg = load_config()
    container = build_container(cfg)
    container.oxigraph.init()

    over_fetch = max(args.n * 5, 500)
    rows = container.oxigraph.sparql_select(_SAMPLE_SPARQL.format(limit=over_fetch))

    seen: set[str] = set()
    unique = []
    for row in rows:
        key = row.get("qid", "")
        if key and key not in seen:
            seen.add(key)
            unique.append(row)

    if len(unique) < args.n:
        print(f"[warn] only {len(unique)} unique links in <urn:tier2>, requested {args.n}")

    sample = random.sample(unique, min(args.n, len(unique)))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for row in sample:
            qid = row.get("qid", "")
            writer.writerow({
                "entity_uri": row.get("entity", ""),
                "wikidata_qid": qid,
                "wikidata_label": row.get("label", ""),
                "enrichment_level": row.get("level", ""),
                "wikidata_url": f"https://www.wikidata.org/wiki/{qid}" if qid else "",
                "correct_disambiguation": "",
                "notes": "",
            })

    print(f"Wrote {len(sample)} links → {out_path}")
    print("Open CSV, visit each wikidata_url, and fill 'correct_disambiguation' (Y/N).")
    print(f"Target precision: ≥{_TARGET:.0%} (PRD §11 п.3)")


def cmd_score(args: argparse.Namespace) -> None:
    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"[error] file not found: {csv_path}")
        sys.exit(1)

    total = correct = unanswered = 0
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            j = row.get("correct_disambiguation", "").strip().upper()
            if j not in ("Y", "N"):
                unanswered += 1
                continue
            total += 1
            correct += j == "Y"

    if total == 0:
        print("[error] no completed judgments (fill Y/N in correct_disambiguation)")
        sys.exit(1)

    precision = correct / total
    passed = precision >= _TARGET

    print(f"\n{'─'*40}")
    print(f"Evaluated:  {total} links")
    print(f"Correct:    {correct} ({precision:.1%})")
    print(f"Incorrect:  {total - correct}")
    if unanswered:
        print(f"Unanswered: {unanswered} (skipped)")
    print(f"Target:     ≥{_TARGET:.0%}")
    print(f"Result:     {'✓ PASS' if passed else '✗ FAIL'}")
    print(f"{'─'*40}\n")

    sys.exit(0 if passed else 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Wikidata disambiguation precision eval")
    sub = parser.add_subparsers(dest="command", required=True)

    p_s = sub.add_parser("sample", help="Sample links and write eval CSV")
    p_s.add_argument("--n", type=int, default=50)
    p_s.add_argument("--out", default="data/eval_wikidata_precision.csv")
    p_s.set_defaults(func=cmd_sample)

    p_sc = sub.add_parser("score", help="Score completed judgment CSV")
    p_sc.add_argument("--csv", default="data/eval_wikidata_precision.csv")
    p_sc.set_defaults(func=cmd_score)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
