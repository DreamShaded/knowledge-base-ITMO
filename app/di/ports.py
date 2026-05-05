"""
DI port abstractions (ADR-0013 §2).
Each port = Protocol with the minimal interface shared across local/remote adapters.
Implementations injected at composition root in app/__init__.py.
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel


class Vector1024(list[float]):
    """Dense embedding vector, always 1024-dim (MRL-truncated if needed)."""


class SparseVec(BaseModel):
    indices: list[int]
    values: list[float]


class ScoredDoc(BaseModel):
    id: str
    score: float
    payload: dict[str, Any] = {}


class LLMMessage(BaseModel):
    role: str  # "system" | "user" | "assistant"
    content: str


class LLMResponse(BaseModel):
    content: str
    model: str
    usage: dict[str, int] = {}


class ParsedChapter(BaseModel):
    slug: str
    content_md: str
    images: list[str] = []
    qa_score_value: float = 1.0  # ratio of alpha chars; low → needs_review


class ParsedBook(BaseModel):
    sha256: str
    title: str
    authors: list[str] = []
    isbn: str = ""
    page_count: int = 0
    chapters: list[ParsedChapter] = []


class ModelArtifact(BaseModel):
    run_id: str
    model_path: str
    metrics: dict[str, float] = {}


class KGEPrediction(BaseModel):
    subject: str
    predicate: str
    object: str
    score: float
    normalized_score: float = 0.0


# ── Protocols ─────────────────────────────────────────────────────────────────

@runtime_checkable
class LLMPort(Protocol):
    async def complete(
        self,
        messages: list[LLMMessage],
        schema: type[BaseModel] | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse: ...


@runtime_checkable
class DenseEmbedderPort(Protocol):
    async def embed_query(self, text: str) -> Vector1024: ...
    async def embed_passages(self, texts: list[str]) -> list[Vector1024]: ...


@runtime_checkable
class SparseEmbedderPort(Protocol):
    async def embed(self, texts: list[str]) -> list[SparseVec]: ...


@runtime_checkable
class RerankerPort(Protocol):
    async def rerank(
        self, query: str, docs: list[str], top_k: int = 10
    ) -> list[ScoredDoc]: ...


@runtime_checkable
class PdfParserPort(Protocol):
    async def parse(self, path: str) -> ParsedBook: ...


@runtime_checkable
class KGEPort(Protocol):
    async def train(self, triples: list[tuple[str, str, str]]) -> ModelArtifact: ...
    async def predict(
        self, model: ModelArtifact, top_k: int = 20
    ) -> list[KGEPrediction]: ...
