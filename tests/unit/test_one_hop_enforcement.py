"""
Unit tests for the hard 1-hop rule (ADR-0008 §4 task 6, phase-11).
Verifies that Q-id values from L1/L2 properties are not expanded further,
and that discovery queries are scoped to tier-0/tier-1 only.
"""
from __future__ import annotations

import pytest

from app.services.wikidata.fetcher_l1 import _qid as extract_qid
from app.services.wikidata.fetcher_l2 import (
    is_wikidata_entity,
    resolve_type_profile,
    _parse_l2,
)
from app.services.wikidata.schemas import WikidataL1, WikidataL2


# ── QID extraction ────────────────────────────────────────────────────────────

class TestExtractQid:
    def test_full_entity_uri(self):
        assert extract_qid("http://www.wikidata.org/entity/Q5") == "Q5"

    def test_numeric_qid(self):
        assert extract_qid("http://www.wikidata.org/entity/Q1374524") == "Q1374524"

    def test_non_entity_uri_returns_empty(self):
        assert extract_qid("http://www.wikidata.org/prop/direct/P31") == ""

    def test_plain_string_returns_empty(self):
        assert extract_qid("Eric Evans") == ""

    def test_empty_string(self):
        assert extract_qid("") == ""

    def test_property_uri_not_qid(self):
        # Prop URIs must not be treated as entity QIDs
        assert extract_qid("http://www.wikidata.org/entity/P31") == ""


# ── is_wikidata_entity guard ──────────────────────────────────────────────────

class TestIsWikidataEntity:
    def test_entity_uri_is_true(self):
        assert is_wikidata_entity("http://www.wikidata.org/entity/Q1374524") is True

    def test_non_wikidata_uri_is_false(self):
        assert is_wikidata_entity("https://my-pkm.local/concept/DDD") is False

    def test_literal_value_is_false(self):
        assert is_wikidata_entity("1954-01-01") is False

    def test_prop_uri_is_false(self):
        # Prop direct URIs are not entity nodes
        assert is_wikidata_entity("http://www.wikidata.org/prop/direct/P50") is False


# ── resolve_type_profile ──────────────────────────────────────────────────────

class TestResolveTypeProfile:
    def _l1(self, instance_of: list[str]) -> WikidataL1:
        return WikidataL1(qid="Q1", instance_of=instance_of)

    def test_human_maps_to_person(self):
        assert resolve_type_profile(self._l1(["Q5"])) == "Person"

    def test_software_maps_correctly(self):
        assert resolve_type_profile(self._l1(["Q7397"])) == "Software"

    def test_book_maps_correctly(self):
        assert resolve_type_profile(self._l1(["Q571"])) == "Book"

    def test_unknown_instance_defaults_to_concept(self):
        assert resolve_type_profile(self._l1(["Q9999999"])) == "Concept"

    def test_empty_instance_of_defaults_to_concept(self):
        assert resolve_type_profile(self._l1([])) == "Concept"

    def test_first_matching_qid_wins(self):
        # Q5 (Person) listed before Q571 (Book) — Person should win
        assert resolve_type_profile(self._l1(["Q5", "Q571"])) == "Person"


# ── 1-hop rule: L2 values are IRI nodes, not re-enriched ─────────────────────

class TestOneHopEnforcement:
    def _make_binding(self, prop_uri: str, value: str, lang: str = "") -> dict:
        o_node: dict = {"value": value}
        if lang:
            o_node["xml:lang"] = lang
        return {"p": {"value": prop_uri}, "o": o_node}

    def test_l2_qid_values_stored_as_uri_strings(self):
        """L2 Q-id values must be stored as raw URIs (not labels), so they can be written as IRIs."""
        bindings = [
            self._make_binding(
                "http://www.wikidata.org/prop/direct/P50",
                "http://www.wikidata.org/entity/Q1374524",
            )
        ]
        l2 = _parse_l2("Q999", "Book", ["P50"], bindings)
        # Value must be the entity URI — caller writes it as an IRI, not a literal
        assert any("Q1374524" in v for v in l2.properties.get("P50", []))

    def test_l2_literal_values_stored_as_strings(self):
        bindings = [
            self._make_binding(
                "http://www.wikidata.org/prop/direct/P577",
                "1999-01-01",
            )
        ]
        l2 = _parse_l2("Q999", "Book", ["P577"], bindings)
        assert l2.properties.get("P577") == ["1999-01-01"]

    def test_l2_value_qid_is_not_wikidata_entity_if_literal(self):
        """Literal date values must NOT be mistaken for entity nodes."""
        assert is_wikidata_entity("1999-01-01") is False

    def test_discovery_does_not_expose_tier2_sparql(self):
        """
        Structural test: verify discovery.py SPARQL patterns are scoped to tier-0/tier-1.
        Read source to ensure GRAPH <urn:tier2> never appears in discovery queries.
        """
        from pathlib import Path
        src = (Path(__file__).parent.parent.parent / "app" / "services" / "wikidata" / "discovery.py").read_text()
        assert "urn:tier2" not in src, (
            "discovery.py must not query tier-2 — would create a re-enrichment loop"
        )
