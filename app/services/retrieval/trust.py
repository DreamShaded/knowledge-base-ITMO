"""Trust multipliers per source type (ADR-0004 §1)."""
from __future__ import annotations

from app.services.retrieval.schemas import Parent

# source_type → multiplier
_MULTIPLIERS: dict[str, float] = {
    "note":          1.00,   # TRUSTED  — personal vault notes
    "book_chapter":  0.95,   # TRUSTED-EXT — curated books
    "web_saved":     0.90,   # TRUSTED-WEB-SAVED — user-approved web content
    "tier1":         0.85,   # SEMI — OpenIE-extracted triples (phase-10)
    "tier2":         0.70,   # UNTRUSTED — KGE predictions
    "web_live":      0.70,   # UNTRUSTED — transient web channel results (phase-07)
}
_DEFAULT_MULTIPLIER = 0.70


def apply_trust_multipliers(parents: list[Parent]) -> list[Parent]:
    """Multiply each parent's score by its source-type trust factor.

    Returns a new list (parents are immutable Pydantic models).
    """
    result: list[Parent] = []
    for p in parents:
        m = _MULTIPLIERS.get(p.source_meta.source_type, _DEFAULT_MULTIPLIER)
        result.append(p.model_copy(update={"score": p.score * m}))
    return result
