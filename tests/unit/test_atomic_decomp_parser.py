"""Unit tests for atomic-decomp.py JSON parser and batch-claim-quote.py merger."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# Load kebab-case modules via importlib
_BASE = Path(__file__).parent.parent.parent / "app/services/faithfulness"


def _load(logical: str, filename: str):
    fqname = f"_faithfulness_test.{logical}"
    if fqname not in sys.modules:
        spec = importlib.util.spec_from_file_location(fqname, _BASE / filename)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        sys.modules[fqname] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return sys.modules[fqname]


_decomp = _load("atomic_decomp", "atomic-decomp.py")
_batch = _load("batch_claim_quote", "batch-claim-quote.py")

parse_claims_json = _decomp.parse_claims_json
merge_verdicts = _batch.merge_verdicts
fallback_verdicts = _batch.fallback_verdicts


# ── parse_claims_json ─────────────────────────────────────────────────────────

class TestParseClaimsJson:
    def test_valid_single_claim(self):
        raw = '{"claims": [{"text": "Python was released in 1991.", "citation_refs": [1], "claim_type": "factual_specific"}]}'
        claims = parse_claims_json(raw)
        assert len(claims) == 1
        assert claims[0].text == "Python was released in 1991."
        assert claims[0].citation_refs == [1]
        assert claims[0].claim_type == "factual_specific"

    def test_multiple_claims(self):
        raw = '{"claims": [{"text": "A", "citation_refs": [], "claim_type": "opinion"}, {"text": "B", "citation_refs": [2, 3], "claim_type": "negation"}]}'
        claims = parse_claims_json(raw)
        assert len(claims) == 2
        assert claims[1].claim_type == "negation"
        assert claims[1].citation_refs == [2, 3]

    def test_missing_citation_refs_defaults_empty(self):
        raw = '{"claims": [{"text": "Fact.", "claim_type": "factual_general"}]}'
        claims = parse_claims_json(raw)
        assert claims[0].citation_refs == []

    def test_invalid_claim_type_defaults_to_factual_general(self):
        raw = '{"claims": [{"text": "Something.", "citation_refs": [], "claim_type": "nonsense"}]}'
        claims = parse_claims_json(raw)
        assert claims[0].claim_type == "factual_general"

    def test_non_int_refs_filtered_out(self):
        raw = '{"claims": [{"text": "X", "citation_refs": [1, "two", 3, null], "claim_type": "framing"}]}'
        claims = parse_claims_json(raw)
        assert claims[0].citation_refs == [1, 3]

    def test_empty_claims_list(self):
        claims = parse_claims_json('{"claims": []}')
        assert claims == []

    def test_missing_claims_key(self):
        claims = parse_claims_json('{"other": []}')
        assert claims == []

    def test_malformed_json_returns_empty(self):
        claims = parse_claims_json("not json")
        assert claims == []

    def test_empty_string_returns_empty(self):
        claims = parse_claims_json("")
        assert claims == []


# ── merge_verdicts ────────────────────────────────────────────────────────────

class TestMergeVerdicts:
    def _make_claim(self, text="fact", refs=None, claim_type="factual_general"):
        from app.services.faithfulness.schemas import AtomicClaim
        return AtomicClaim(text=text, citation_refs=refs or [], claim_type=claim_type)

    def test_full_verdict_no_warning(self):
        claims = [self._make_claim("A")]
        results = [{"claim_index": 0, "verdict": "full", "reasoning_short": "ok"}]
        verdicts = merge_verdicts(claims, results)
        assert verdicts[0].verdict == "full"
        assert verdicts[0].warning is False

    def test_partial_verdict_has_warning(self):
        claims = [self._make_claim("B")]
        results = [{"claim_index": 0, "verdict": "partial", "reasoning_short": "missing detail"}]
        verdicts = merge_verdicts(claims, results)
        assert verdicts[0].verdict == "partial"
        assert verdicts[0].warning is True

    def test_none_verdict_has_warning(self):
        claims = [self._make_claim("C")]
        results = [{"claim_index": 0, "verdict": "none", "reasoning_short": "no support"}]
        verdicts = merge_verdicts(claims, results)
        assert verdicts[0].verdict == "none"
        assert verdicts[0].warning is True

    def test_unknown_verdict_treated_as_none(self):
        claims = [self._make_claim("D")]
        results = [{"claim_index": 0, "verdict": "maybe", "reasoning_short": ""}]
        verdicts = merge_verdicts(claims, results)
        assert verdicts[0].verdict == "none"
        assert verdicts[0].warning is True

    def test_missing_result_defaults_to_none(self):
        claims = [self._make_claim("E"), self._make_claim("F")]
        results = [{"claim_index": 0, "verdict": "full", "reasoning_short": "ok"}]
        verdicts = merge_verdicts(claims, results)
        assert verdicts[0].verdict == "full"
        assert verdicts[1].verdict == "none"  # missing → none

    def test_fallback_verdicts_all_warning(self):
        claims = [self._make_claim("X"), self._make_claim("Y")]
        verdicts = fallback_verdicts(claims)
        assert all(v.warning for v in verdicts)
        assert all(v.verdict == "none" for v in verdicts)
        assert all(v.reasoning_short == "llm_error" for v in verdicts)
