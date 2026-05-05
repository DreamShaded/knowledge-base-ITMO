"""OpenIE data models: CandidateTriple, GateResult, RoutingDecision (ADR-0008 §3)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CandidateTriple(BaseModel):
    subject_uri: str
    predicate: str  # full URI
    object_uri_or_literal: str
    evidence_chunk_id: str
    confidence_self_reported: float = Field(ge=0.0, le=1.0)
    claim_type: Literal[
        "factual_specific", "factual_general", "framing", "opinion"
    ] = "factual_general"
    is_object_literal: bool = False
    is_new_subject: bool = False  # True if subject not found in existing graph
    is_new_object: bool = False   # True if object not found in existing graph


class GateResult(BaseModel):
    passed: bool
    gate_id: int  # 1–5
    reason: str = ""
    modified_confidence: float | None = None
    canonical_uri: str | None = None  # Gate 3: resolved existing URI


class RoutingDecision(BaseModel):
    destination: Literal["tier1", "tier1-pending", "tier1-rejected"]
    final_confidence: float
    gates: list[GateResult]
    reason: str = ""
    conflict: bool = False
    provenance: str = "auto"  # "auto" written to pkm:provenance for promoted triples
