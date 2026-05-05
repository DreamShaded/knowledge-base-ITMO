"""RouterDecision schema and RetrievalContext (ADR-0004 §2)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

QueryIntent = Literal[
    "lookup", "multi_hop", "personal", "educational", "fresh", "comparison", "refusal"
]

_DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "lookup":      {"notes": 0.7, "books": 0.5, "wikidata": 0.8, "web": 0.0},
    "multi_hop":   {"notes": 1.0, "books": 0.7, "wikidata": 0.3, "web": 0.0},
    "personal":    {"notes": 1.0, "books": 0.3, "wikidata": 0.0, "web": 0.0},
    "educational": {"notes": 0.6, "books": 0.9, "wikidata": 0.5, "web": 0.0},
    "fresh":       {"notes": 0.5, "books": 0.2, "wikidata": 0.3, "web": 0.8},
    "comparison":  {"notes": 0.8, "books": 0.8, "wikidata": 0.6, "web": 0.0},
    "refusal":     {"notes": 0.0, "books": 0.0, "wikidata": 0.0, "web": 0.0},
}


class RouterDecision(BaseModel):
    intent: QueryIntent = "lookup"
    channel_weights: dict[str, float] = Field(
        default_factory=lambda: {"notes": 1.0, "books": 0.7, "wikidata": 0.0, "web": 0.0}
    )
    entities: list[str] = Field(default_factory=list)
    reformulations: list[str] = Field(default_factory=list)
    web_safe_query: str = ""
    refusal: bool = False

    @classmethod
    def from_intent(cls, intent: QueryIntent) -> "RouterDecision":
        """Build a RouterDecision with default weights for the given intent."""
        return cls(
            intent=intent,
            channel_weights=_DEFAULT_WEIGHTS.get(intent, _DEFAULT_WEIGHTS["lookup"]),
            refusal=(intent == "refusal"),
        )


class RetrievalContext(BaseModel):
    """Mutable context passed through the query pipeline."""

    query: str
    router_decision: RouterDecision | None = None

    # Scope from /scope command
    vault_scope: list[str] | str = "all"

    # Command flags
    force_web: bool = False
    no_web: bool = False             # /no-web disables auto-trigger too
    save_web_url: str = ""           # /save-web URL target
    strict_mode: bool = False        # /strict disables Z3
    skip_faithfulness: bool = False  # /no-check
    sources_only: bool = False       # /sources
    sources_filter: list[str] = []   # /sources:notes,books — channel allow-list
    explain_mode: bool = False       # /explain
    deep_mode: bool = False          # /deep
    explore_mode: bool = False       # /explore
