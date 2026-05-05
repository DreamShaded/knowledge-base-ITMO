"""Unit tests for faithfulness warning rendering: threshold logic and ⚠ marker insertion."""
from __future__ import annotations

import pytest

from app.services.faithfulness.schemas import AtomicClaim, ClaimVerdict, FaithfulnessResult
from app.services.faithfulness.pipeline import _compute_result
from app.services.render.markdown import apply_claim_warnings, MarkdownRenderer


# ── Helpers ───────────────────────────────────────────────────────────────────

def _verdict(verdict: str, refs: list[int] = None, claim_type: str = "factual_general") -> ClaimVerdict:
    claim = AtomicClaim(text="x", citation_refs=refs or [], claim_type=claim_type)
    return ClaimVerdict(claim=claim, verdict=verdict, warning=verdict in ("partial", "none"))


def _envelope(z1_content: str = "Ответ [^1].", has_citations: bool = True):
    """Minimal AnswerEnvelope for rendering tests."""
    from app.services.generator import AnswerEnvelope, Section, Citation, ConfidenceSignal
    sections = [Section(zone="Z1", content=z1_content)]
    citations = [Citation(id=1, source_id="note/a.md", source_type="note", snippet="evidence")] if has_citations else []
    return AnswerEnvelope(
        tldr="Summary",
        sections=sections,
        citations=citations,
        confidence_signal=ConfidenceSignal(level="medium"),
    )


# ── _compute_result: aggregate threshold ─────────────────────────────────────

class TestComputeResult:
    def test_no_warnings_below_threshold(self):
        verdicts = [_verdict("full"), _verdict("full"), _verdict("none")]
        result = _compute_result(verdicts)
        # 1/3 = 33% < 50% → no aggregate
        assert result.aggregate_warning is False
        assert pytest.approx(result.warning_fraction, abs=0.01) == 1 / 3

    def test_exactly_50_percent_triggers_aggregate(self):
        verdicts = [_verdict("none"), _verdict("none"), _verdict("full"), _verdict("full")]
        result = _compute_result(verdicts)
        assert result.aggregate_warning is True
        assert result.warning_fraction == 0.5

    def test_all_warnings_triggers_aggregate(self):
        verdicts = [_verdict("none"), _verdict("partial"), _verdict("none")]
        result = _compute_result(verdicts)
        assert result.aggregate_warning is True
        assert result.warning_fraction == 1.0

    def test_zero_warnings_no_aggregate(self):
        verdicts = [_verdict("full"), _verdict("full")]
        result = _compute_result(verdicts)
        assert result.aggregate_warning is False
        assert result.warning_fraction == 0.0

    def test_empty_verdicts_no_aggregate(self):
        result = _compute_result([])
        assert result.aggregate_warning is False
        assert result.verdicts == []

    def test_49_percent_no_aggregate(self):
        # 49 none + 51 full = 49% warning
        verdicts = [_verdict("none")] * 49 + [_verdict("full")] * 51
        result = _compute_result(verdicts)
        assert result.aggregate_warning is False

    def test_51_percent_triggers_aggregate(self):
        verdicts = [_verdict("none")] * 51 + [_verdict("full")] * 49
        result = _compute_result(verdicts)
        assert result.aggregate_warning is True


# ── apply_claim_warnings: ⚠ marker insertion ─────────────────────────────────

class TestApplyClaimWarnings:
    def test_marks_warning_ref(self):
        result = apply_claim_warnings("See [^1] for details.", {1})
        assert "[^1] ⚠" in result

    def test_leaves_non_warning_ref_unchanged(self):
        result = apply_claim_warnings("See [^2] for details.", {1})
        assert "[^2]" in result
        assert "[^2] ⚠" not in result

    def test_marks_only_warning_refs_in_mixed_text(self):
        text = "Fact A [^1] and fact B [^2]."
        result = apply_claim_warnings(text, {2})
        assert "[^1]" in result and "[^1] ⚠" not in result
        assert "[^2] ⚠" in result

    def test_does_not_double_mark(self):
        text = "Already [^1] ⚠ marked."
        result = apply_claim_warnings(text, {1})
        assert result.count("⚠") == 1

    def test_empty_warning_refs_no_change(self):
        text = "Clean [^1] text."
        assert apply_claim_warnings(text, set()) == text

    def test_no_refs_in_text_no_change(self):
        text = "No citations here."
        assert apply_claim_warnings(text, {1, 2}) == text


# ── MarkdownRenderer integration with FaithfulnessResult ─────────────────────

class TestMarkdownRendererFaithfulness:
    def test_no_faithfulness_no_warning_block(self):
        env = _envelope()
        md = MarkdownRenderer().render(env)
        assert "⚠" not in md

    def test_aggregate_warning_appears_above_ответ(self):
        env = _envelope()
        faith = FaithfulnessResult(
            verdicts=[_verdict("none", refs=[1]), _verdict("none", refs=[1])],
            aggregate_warning=True,
            warning_fraction=1.0,
        )
        md = MarkdownRenderer().render(env, faithfulness=faith)
        assert "⚠" in md
        assert "Ответ может содержать" in md
        # aggregate warning must appear before the ## Ответ heading
        assert md.index("Ответ может содержать") < md.index("## Ответ")

    def test_per_claim_warning_marks_citation_ref(self):
        env = _envelope("Факт [^1] подтверждён.")
        faith = FaithfulnessResult(
            verdicts=[_verdict("none", refs=[1])],
            aggregate_warning=False,
            warning_fraction=1.0,
        )
        md = MarkdownRenderer().render(env, faithfulness=faith)
        assert "[^1] ⚠" in md

    def test_skipped_faithfulness_no_warnings(self):
        env = _envelope("Факт [^1].")
        faith = FaithfulnessResult(skipped=True, skip_reason="no-check")
        md = MarkdownRenderer().render(env, faithfulness=faith)
        assert "⚠" not in md

    def test_well_grounded_response_zero_warnings(self):
        """Sentinel S07: all claims full → no ⚠."""
        env = _envelope("Факт [^1].")
        faith = FaithfulnessResult(
            verdicts=[_verdict("full", refs=[1])],
            aggregate_warning=False,
            warning_fraction=0.0,
        )
        md = MarkdownRenderer().render(env, faithfulness=faith)
        assert "⚠" not in md

    def test_unsupported_claim_gets_warning(self):
        """Sentinel S08: unsupported claim → ⚠ present."""
        env = _envelope("Утверждение [^1] без поддержки.")
        faith = FaithfulnessResult(
            verdicts=[_verdict("none", refs=[1])],
            aggregate_warning=False,
            warning_fraction=1.0,
        )
        md = MarkdownRenderer().render(env, faithfulness=faith)
        assert "⚠" in md
