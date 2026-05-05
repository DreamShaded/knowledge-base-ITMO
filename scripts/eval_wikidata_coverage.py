#!/usr/bin/env python3
"""
Wikidata enrichment coverage check (phase-11, PRD §11 п.3).
Reports % of Concepts in vault that have pkm:enrichmentLevel in <urn:tier2>.
Target: ≥30% (PRD §11 п.3 acceptance criterion).

Usage:
  python scripts/eval_wikidata_coverage.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

_TARGET = 0.30

_TOTAL_CONCEPTS_SPARQL = """
PREFIX pkm: <https://my-pkm.local/ontology#>
SELECT (COUNT(DISTINCT ?concept) AS ?n) WHERE {
  GRAPH ?g {
    ?concept a pkm:Concept .
    FILTER(?g IN (<urn:tier0>, <urn:tier1>))
  }
}
"""

_ENRICHED_CONCEPTS_SPARQL = """
PREFIX pkm: <https://my-pkm.local/ontology#>
SELECT (COUNT(DISTINCT ?concept) AS ?n) WHERE {
  GRAPH <urn:tier2> {
    ?concept pkm:enrichmentLevel ?level .
    FILTER(?level IN ("L1", "L1+L2"))
  }
}
"""


def main() -> None:
    from app.config_profile import load_config
    from app.di.composition import build_container

    cfg = load_config()
    container = build_container(cfg)
    container.oxigraph.init()

    total_rows = container.oxigraph.sparql_select(_TOTAL_CONCEPTS_SPARQL)
    enriched_rows = container.oxigraph.sparql_select(_ENRICHED_CONCEPTS_SPARQL)

    total = int(total_rows[0].get("n", "0") or "0") if total_rows else 0
    enriched = int(enriched_rows[0].get("n", "0") or "0") if enriched_rows else 0

    if total == 0:
        print("[warn] No Concepts found in tier-0/tier-1 graphs. Run indexing first.")
        sys.exit(1)

    coverage = enriched / total
    passed = coverage >= _TARGET

    print(f"\n{'─'*40}")
    print(f"Total concepts:    {total}")
    print(f"Enriched (L1/L2):  {enriched} ({coverage:.1%})")
    print(f"Target:            ≥{_TARGET:.0%}")
    print(f"Result:            {'✓ PASS' if passed else '✗ FAIL'}")
    print(f"{'─'*40}\n")

    if not passed:
        gap = _TARGET - coverage
        print(f"  Coverage below target by {gap:.1%}.")
        print("  Run wikidata enrichment job or check disambiguation thresholds.")
    sys.exit(0 if passed else 1)


if __name__ == "__main__":
    main()
