"""Faithfulness pipeline schemas (ADR-0007, phase-09)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AtomicClaim(BaseModel):
    text: str
    citation_refs: list[int] = Field(default_factory=list)  # [^N] reference numbers
    claim_type: Literal[
        "factual_specific", "factual_general", "opinion", "framing", "negation"
    ] = "factual_general"


class ClaimVerdict(BaseModel):
    claim: AtomicClaim
    verdict: Literal["full", "partial", "none"] = "none"
    warning: bool = False  # True if claim needs ⚠ marker
    reasoning_short: str = ""


class FaithfulnessResult(BaseModel):
    verdicts: list[ClaimVerdict] = Field(default_factory=list)
    aggregate_warning: bool = False   # True if ≥50% claims have warning
    warning_fraction: float = 0.0
    skipped: bool = False
    skip_reason: str = ""
    from_cache: bool = False
