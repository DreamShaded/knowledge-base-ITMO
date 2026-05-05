"""Output schemas for the hybrid retrieval pipeline (ADR-0002)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChildMatch(BaseModel):
    chunk_id: str
    score: float
    content: str


class SourceMeta(BaseModel):
    source_type: str   # "note" | "book_chapter" | "web_saved"
    source_id: str
    vault_id: str
    trust_tier: str
    channel: str       # "notes" | "books" | "web_saved" | "graph_walk"


class Parent(BaseModel):
    parent_id: str
    content: str
    score: float
    source_meta: SourceMeta
    child_matches: list[ChildMatch] = Field(default_factory=list)
    # Dense embedding for MMR — excluded from API serialization
    embedding: list[float] = Field(default_factory=list, exclude=True)


class GraphPath(BaseModel):
    entities: list[str]
    predicates: list[str]
    path_score: float
    content: str
    source_id: str


class WikidataEntity(BaseModel):
    uri: str
    label: str
    score: float


class EmptyRetrieval(BaseModel):
    reason: str = "no_results"


class RetrievalResult(BaseModel):
    query: str
    parents: list[Parent] = Field(default_factory=list)
    graph_paths: list[GraphPath] = Field(default_factory=list)
    wikidata_entities: list[WikidataEntity] = Field(default_factory=list)
