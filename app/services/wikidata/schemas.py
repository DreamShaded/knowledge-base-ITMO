"""Wikidata enrichment data models (ADR-0008 §4)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EntityCandidate(BaseModel):
    """Surface form collected from discovery, ready for Wikidata disambiguation."""
    surface_form: str
    context_chunks: list[str] = Field(default_factory=list)
    type_hint: Literal["Person", "Software", "Concept", "Book", "Organization", "Event", ""] = ""


class WikidataSearchResult(BaseModel):
    """Single candidate returned by wbsearchentities."""
    qid: str
    label: str
    description: str = ""
    aliases: list[str] = Field(default_factory=list)


class DisambiguationResult(BaseModel):
    """Output of the two-step disambiguation pipeline."""
    qid: str
    label: str
    confidence: float = Field(ge=0.0, le=1.0)
    method: Literal["auto", "llm", "skipped"]
    surface_form: str


class WikidataL1(BaseModel):
    """L1 properties: always fetched regardless of entity type."""
    qid: str
    labels: dict[str, str] = Field(default_factory=dict)       # lang → text
    descriptions: dict[str, str] = Field(default_factory=dict) # lang → text
    aliases: dict[str, list[str]] = Field(default_factory=dict) # lang → [text]
    instance_of: list[str] = Field(default_factory=list)        # P31 Q-ids
    subclass_of: list[str] = Field(default_factory=list)        # P279 Q-ids


class WikidataL2(BaseModel):
    """L2 type-specific properties."""
    qid: str
    type_profile: str  # Person | Software | Concept | Book | Organization | Event
    properties: dict[str, list[str]] = Field(default_factory=dict)  # P-id → [values]


class WikidataL3(BaseModel):
    """L3 lazy runtime enrichment — NOT persisted to graph, cached 7d in SQLite."""
    qid: str
    extended_description: str = ""
    deep_hierarchy: list[str] = Field(default_factory=list)  # up to 2-hop P279 labels
