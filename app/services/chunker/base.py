"""Core chunk data models used by all chunker implementations."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Chunk:
    """Leaf/child chunk — indexed as a Qdrant point."""

    chunk_id: str          # stable_hash(source_id + chunk_index)
    parent_id: str         # ID of the parent chunk this leaf belongs to
    source_type: str       # "note" | "book_chapter" | "web_saved"
    source_id: str         # unique source identifier
    vault_id: str          # vault slug or "" for books/web
    chapter_id: str        # book chapter slug or "" for notes/web
    project: str           # project tag or ""
    tags: list[str]        # free-form tags
    trust_tier: str        # "tier0" | "tier1" | "tier2"
    content: str           # raw text of this chunk
    content_hash: str      # sha256[:16] of content for idempotency
    chunk_index: int       # ordinal within the source
    modified_at: str       # ISO-8601 timestamp or ""
    removed: bool = False


@dataclass
class ParentChunk:
    """Parent chunk — stored in Qdrant with same named-vectors schema."""

    chunk_id: str
    parent_id: str         # "" for top-level parents
    source_type: str
    source_id: str
    vault_id: str
    chapter_id: str
    project: str
    tags: list[str]
    trust_tier: str
    content: str
    content_hash: str
    chunk_index: int
    modified_at: str
    removed: bool = False
    is_parent: bool = True


@dataclass
class ChunkedDocument:
    """Result of chunking a single source document."""

    parents: list[ParentChunk] = field(default_factory=list)
    children: list[Chunk] = field(default_factory=list)
