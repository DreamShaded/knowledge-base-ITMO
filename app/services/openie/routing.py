"""
Routing matrix: gate results → tier1 / tier1-pending / tier1-rejected (ADR-0008 §3).

Decision table:
  All 5 gates pass              → tier1 (auto-promote)
  Gate 2 or 5 hard-fail        → tier1-rejected
  Gate 1 conf < hard_reject    → tier1-rejected
  Gate 3 grey zone             → tier1-pending
  Gate 4 single-source         → tier1-pending (if otherwise borderline)
  Gate 1 pass but conf < thresh → tier1-pending (0.60 ≤ conf < threshold)
  Conflict flag (Gate 5)       → tier1-pending with conflict=True
"""
from __future__ import annotations

from app.services.openie.schemas import CandidateTriple, GateResult, RoutingDecision

_HARD_REJECT = 0.60  # below this → always rejected


def route(triple: CandidateTriple, gates: list[GateResult]) -> RoutingDecision:
    """Apply routing matrix and return the destination + metadata."""
    by_id = {g.gate_id: g for g in gates}

    g1 = by_id.get(1)
    g2 = by_id.get(2)
    g3 = by_id.get(3)
    g4 = by_id.get(4)
    g5 = by_id.get(5)

    # Compute effective final confidence from gate results (Gate 4 may boost)
    final_conf = triple.confidence_self_reported
    if g4 and g4.modified_confidence is not None:
        final_conf = g4.modified_confidence
    elif g1 and g1.modified_confidence is not None:
        final_conf = g1.modified_confidence

    # Hard rejects: Gate 2 fail or Gate 5 hard-fail (not conflict) or conf < HARD_REJECT
    if g2 and not g2.passed:
        return RoutingDecision(
            destination="tier1-rejected",
            final_confidence=final_conf,
            gates=gates,
            reason=f"gate2_reject: {g2.reason}",
        )

    if g5 and not g5.passed:
        return RoutingDecision(
            destination="tier1-rejected",
            final_confidence=final_conf,
            gates=gates,
            reason=f"gate5_reject: {g5.reason}",
        )

    if triple.confidence_self_reported < _HARD_REJECT:
        return RoutingDecision(
            destination="tier1-rejected",
            final_confidence=final_conf,
            gates=gates,
            reason=f"conf_below_hard_reject: {triple.confidence_self_reported:.2f}",
        )

    # Soft pending: grey canonicalization, single-source borderline, gate1 soft-fail, conflict
    conflict = bool(g5 and g5.reason == "conflict_flagged")

    if g3 and not g3.passed:
        return RoutingDecision(
            destination="tier1-pending",
            final_confidence=final_conf,
            gates=gates,
            reason="gate3_grey_zone",
            conflict=conflict,
        )

    if g1 and not g1.passed:
        # conf ≥ HARD_REJECT but < predicate threshold → pending
        return RoutingDecision(
            destination="tier1-pending",
            final_confidence=final_conf,
            gates=gates,
            reason=f"gate1_soft_fail: {g1.reason}",
            conflict=conflict,
        )

    if conflict:
        return RoutingDecision(
            destination="tier1-pending",
            final_confidence=final_conf,
            gates=gates,
            reason="conflict_with_existing",
            conflict=True,
        )

    # Single-source without a confidence boost — only send to pending if confidence is
    # in the borderline range [0.60, effective_threshold). Gate 1 passed means we're
    # already above threshold, so single-source alone doesn't block auto-promote.

    return RoutingDecision(
        destination="tier1",
        final_confidence=final_conf,
        gates=gates,
        reason="all_gates_passed",
        provenance="auto",
    )
