"""
Multi-signal web channel trigger evaluation (ADR-0004 §3).
Returns True if ANY trigger condition is met and no /no-web override.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.retrieval.schemas import GraphPath, Parent

_LOW_SCORE_THRESHOLD = 0.4
_LOW_TOKEN_THRESHOLD = 500


def should_trigger_web(
    force_web: bool,
    no_web: bool,
    intent: str,
    parents: "list[Parent]",
    graph_paths: "list[GraphPath]",
    router_entities: list[str],
) -> bool:
    """
    Evaluate multi-signal web trigger.

    /no-web override → always False.
    /web  override  → always True.
    Otherwise: ANY of the 5 signal conditions fires.
    """
    if no_web:
        return False
    if force_web:
        return True

    # Signal 1: intent == "fresh"
    if intent == "fresh":
        return True

    # Signal 2: intent == "explore" with fewer than 3 parents
    if intent == "explore" and len(parents) < 3:
        return True

    # Signal 3: top local score too low
    if not parents or parents[0].score < _LOW_SCORE_THRESHOLD:
        return True

    # Signal 4: total content too thin
    total_tokens = sum(len(p.content.split()) for p in parents)
    if total_tokens < _LOW_TOKEN_THRESHOLD:
        return True

    # Signal 5: router found entities but graph walk returned nothing
    if router_entities and not graph_paths:
        return True

    return False
