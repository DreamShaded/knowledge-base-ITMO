"""
Cohen's kappa computation (ADR-0010 §6, phase-14 task 6).
Inter-rater agreement between human author and LLM-judge.
Target: κ ≥ 0.6 (PRD §4.2).
"""
from __future__ import annotations

from app.logging import get_logger

log = get_logger(__name__)


def cohens_kappa(rater_a: list[int], rater_b: list[int]) -> float:
    """
    Compute Cohen's κ for two raters over the same N items.
    Labels are integers (0/1 binary, or 1-5 Likert).
    Returns κ ∈ [-1, 1]. Raises ValueError on mismatched lengths.
    """
    if len(rater_a) != len(rater_b):
        raise ValueError(
            f"Rater lists must have equal length: {len(rater_a)} != {len(rater_b)}"
        )
    if not rater_a:
        return 0.0

    n = len(rater_a)
    categories = sorted(set(rater_a) | set(rater_b))

    # Observed agreement
    po = sum(1 for a, b in zip(rater_a, rater_b) if a == b) / n

    # Expected agreement by chance
    pe = sum(
        (sum(1 for x in rater_a if x == cat) / n)
        * (sum(1 for x in rater_b if x == cat) / n)
        for cat in categories
    )

    if pe >= 1.0:
        return 1.0

    kappa = (po - pe) / (1.0 - pe)
    log.info("cohens_kappa", kappa=round(kappa, 4), n=n,
             po=round(po, 4), pe=round(pe, 4))
    return kappa


def interpret_kappa(kappa: float) -> str:
    """Human-readable interpretation of κ value (Landis & Koch 1977)."""
    if kappa < 0:
        return "worse_than_chance"
    if kappa < 0.20:
        return "slight"
    if kappa < 0.40:
        return "fair"
    if kappa < 0.60:
        return "moderate"
    if kappa < 0.80:
        return "substantial"
    return "almost_perfect"
