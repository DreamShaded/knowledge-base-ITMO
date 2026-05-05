"""AnswerEnvelope and related schemas (ADR-0006 §1)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Citation(BaseModel):
    id: int  # inline reference number, e.g. [^1]
    source_id: str
    source_type: str  # "note" | "book_chapter" | "web_saved"
    vault_id: str = ""
    title: str = ""
    snippet: str = ""
    score: float = 0.0
    modified_at: str = ""
    trust_tier: str = ""


class Section(BaseModel):
    zone: Literal["Z1", "Z2", "Z3"]
    heading: str = ""
    content: str  # Markdown; Z1 must include [^N] citation markers


class ConfidenceSignal(BaseModel):
    level: Literal["high", "medium", "low"] = "medium"
    reason: str = ""
    retrieval_hit_count: int = 0


class AnswerEnvelope(BaseModel):
    tldr: str = Field(description="One-sentence summary of the answer")
    sections: list[Section] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list, description="Related topic suggestions")
    citations: list[Citation] = Field(default_factory=list)
    confidence_signal: ConfidenceSignal = Field(
        default_factory=ConfidenceSignal
    )
    prompt_version: str = ""
    refusal: bool = False
    refusal_message: str = ""

    def to_markdown(self, *, vault_map: dict = {}, base_url: str = "http://localhost:8000", query: str = "") -> str:
        """Render envelope as Markdown string for SSE streaming.

        Delegates to MarkdownRenderer for full structured output.
        Parameters are optional for backward-compat with existing callers.
        """
        try:
            from app.services.render.markdown import MarkdownRenderer
            return MarkdownRenderer().render(self, vault_map=vault_map, base_url=base_url, query=query)
        except ImportError:
            # Fallback: simple legacy rendering (keeps tests passing if render pkg unavailable)
            return self._legacy_markdown()

    def _legacy_markdown(self) -> str:
        """Minimal legacy rendering used as import fallback."""
        parts: list[str] = []
        if self.refusal:
            parts.append(f"> {self.refusal_message or 'Не нашёл в источниках.'}")
            return "\n".join(parts)
        parts.append(f"**{self.tldr}**\n")
        for section in self.sections:
            if section.heading:
                parts.append(f"\n### {section.heading}\n")
            parts.append(section.content)
        if self.related:
            parts.append("\n\n**Связанные темы:** " + ", ".join(self.related))
        if self.citations:
            parts.append("\n\n---")
            for c in self.citations:
                label = c.title or c.source_id
                parts.append(f"[^{c.id}]: {label} — {c.snippet}")
        return "\n".join(parts)
