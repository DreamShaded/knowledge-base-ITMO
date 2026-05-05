#!/usr/bin/env python3
"""
Manual rubric evaluation for KGE top-50 surfaced predictions (PRD §11 p.5, ADR-0009 §10).

Samples up to 50 predictions from kge_review_queue and generates a CSV with
3 Y/N rubric questions per prediction. Evaluator fills in the CSV 1-2 weeks
after generation to reduce mindset bias.

Usage:
    python scripts/eval-kge-top50.py [--db PATH] [--run-id RUN_ID] [--out PATH]

Output CSV columns:
    id, head_label, relation, tail_label, score, zscore,
    Q1_relation_type_plausible (Y/N/blank),
    Q2_entities_relate (Y/N/blank),
    Q3_relation_valuable_to_discover (Y/N/blank),
    notes
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

_RUBRIC_QUESTIONS = [
    "Q1_relation_type_plausible",       # Is the relation type plausible?
    "Q2_entities_relate",                # Do the entities actually relate?
    "Q3_relation_valuable_to_discover",  # Is the relation valuable to discover?
]

_DEFAULT_DB = _REPO_ROOT / "data" / "sqlite" / "app.db"
_DEFAULT_OUT = _REPO_ROOT / "data" / "kge" / "eval-top50.csv"
_SAMPLE_SIZE = 50


def main() -> None:
    parser = argparse.ArgumentParser(description="KGE top-50 rubric eval scaffold")
    parser.add_argument("--db", default=str(_DEFAULT_DB), help="SQLite path")
    parser.add_argument("--run-id", default=None, help="Limit to a specific run_id")
    parser.add_argument("--out", default=str(_DEFAULT_OUT), help="Output CSV path")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    query = """
        SELECT id, run_id, head_uri, predicted_rel, tail_uri,
               score, zscore, head_label, tail_label, justification, status
        FROM kge_review_queue
    """
    params: list = []
    if args.run_id:
        query += " WHERE run_id = ?"
        params.append(args.run_id)
    query += " ORDER BY zscore DESC LIMIT ?"
    params.append(_SAMPLE_SIZE)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        print("No predictions found in kge_review_queue.", file=sys.stderr)
        sys.exit(1)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "id", "run_id",
        "head_label", "predicted_relation", "tail_label",
        "score", "zscore", "justification", "status",
    ] + _RUBRIC_QUESTIONS + ["notes"]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            h_label = row["head_label"] or row["head_uri"].split("/")[-1]
            t_label = row["tail_label"] or row["tail_uri"].split("/")[-1]
            rel = row["predicted_rel"].split("#")[-1].split("/")[-1]
            writer.writerow({
                "id": row["id"],
                "run_id": row["run_id"],
                "head_label": h_label,
                "predicted_relation": rel,
                "tail_label": t_label,
                "score": f"{row['score']:.4f}",
                "zscore": f"{row['zscore']:.3f}",
                "justification": row["justification"] or "",
                "status": row["status"],
                # Rubric columns — left blank for manual evaluation
                "Q1_relation_type_plausible": "",
                "Q2_entities_relate": "",
                "Q3_relation_valuable_to_discover": "",
                "notes": "",
            })

    print(f"Wrote {len(rows)} predictions to {out_path}")
    print("Fill in Y/N for Q1-Q3 columns, then run precision calculation.")
    _print_instructions(out_path)


def _print_instructions(csv_path: Path) -> None:
    print(f"""
Rubric instructions (ADR-0009 §10):
  Q1: Is the predicted relation type semantically plausible?
  Q2: Do the two entities actually have a meaningful relationship?
  Q3: Is discovering this relation valuable for your knowledge base?

Precision@50 = (rows where ALL 3 = Y) / {_SAMPLE_SIZE}
Target: ≥ 0.50 (PRD §11 p.5, deferred eval 1-2 weeks after generation)

CSV: {csv_path}
""")


if __name__ == "__main__":
    main()
