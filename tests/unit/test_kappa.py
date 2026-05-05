"""
Tests for Cohen's kappa computation (phase-14, ADR-0010 §6).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from app.services.eval.kappa import cohens_kappa, interpret_kappa


# ── cohens_kappa ───────────────────────────────────────────────────────────────

def test_perfect_agreement():
    a = [1, 0, 1, 1, 0]
    assert cohens_kappa(a, a) == pytest.approx(1.0)


def test_complete_disagreement_binary():
    a = [0, 0, 1, 1]
    b = [1, 1, 0, 0]
    kappa = cohens_kappa(a, b)
    # Perfect disagreement on balanced binary → κ = -1
    assert kappa == pytest.approx(-1.0)


def test_chance_agreement_returns_zero():
    # po == pe when each rater independently assigns 50/50 labels with no correlation.
    # a=[0,1,0,1], b=[0,0,1,1]: po=0.5, pe=0.5*0.5+0.5*0.5=0.5 → κ=0
    a = [0, 1, 0, 1]
    b = [0, 0, 1, 1]
    kappa = cohens_kappa(a, b)
    assert kappa == pytest.approx(0.0, abs=1e-9)


def test_kappa_raises_on_mismatched_lengths():
    with pytest.raises(ValueError, match="equal length"):
        cohens_kappa([1, 2, 3], [1, 2])


def test_kappa_empty_lists_returns_zero():
    assert cohens_kappa([], []) == pytest.approx(0.0)


def test_kappa_range():
    a = [1, 1, 0, 1, 0, 1]
    b = [1, 0, 0, 1, 1, 1]
    kappa = cohens_kappa(a, b)
    assert -1.0 <= kappa <= 1.0


def test_kappa_likert_scale():
    a = [1, 2, 3, 4, 5, 3]
    b = [1, 2, 3, 4, 4, 3]  # one disagreement (5 vs 4)
    kappa = cohens_kappa(a, b)
    assert kappa > 0.6  # high agreement expected


def test_kappa_single_item_perfect():
    assert cohens_kappa([1], [1]) == pytest.approx(1.0)


def test_kappa_all_same_label():
    # All raters agree on same label → pe = 1.0, kappa clamped to 1.0
    a = [1, 1, 1, 1]
    b = [1, 1, 1, 1]
    kappa = cohens_kappa(a, b)
    assert kappa == pytest.approx(1.0)


# ── interpret_kappa ────────────────────────────────────────────────────────────

def test_interpret_almost_perfect():
    assert interpret_kappa(0.85) == "almost_perfect"


def test_interpret_substantial():
    assert interpret_kappa(0.65) == "substantial"


def test_interpret_moderate():
    assert interpret_kappa(0.50) == "moderate"


def test_interpret_fair():
    assert interpret_kappa(0.30) == "fair"


def test_interpret_slight():
    assert interpret_kappa(0.10) == "slight"


def test_interpret_worse_than_chance():
    assert interpret_kappa(-0.05) == "worse_than_chance"


def test_target_threshold_is_substantial():
    # κ = 0.6 is the boundary between moderate and substantial
    assert interpret_kappa(0.60) == "substantial"
