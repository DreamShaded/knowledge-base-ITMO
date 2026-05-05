"""
Web channel: SearXNG search → extraction → embed → Parent objects (ADR-0005).
Transient results — not stored in Qdrant.
trust_tier=UNTRUSTED, multiplier ×0.7 applied downstream by trust module.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.logging import get_logger
from app.services.retrieval.schemas import ChildMatch, Parent, SourceMeta
from app.services.web.extraction import extract_page
from app.services.web.health import WebChannelHealth
from app.services.web.quota import DailyQuota
from app.services.web.sanitization import sanitize_query
from app.services.web.search import SearXNGSearch, WebSearchResult

if TYPE_CHECKING:
    from app.di.ports import DenseEmbedderPort
    from app.stores.sqlite import SQLiteStore

log = get_logger(__name__)

_WEB_CHANNEL = "web"
_WEB_SOURCE_TYPE = "web_live"
_WEB_VAULT_ID = "_web"
_TRUST_TIER = "UNTRUSTED"


def _dot_product(a: list[float], b: list[float]) -> float:
    """Cosine similarity for pre-normalized vectors = dot product."""
    return sum(x * y for x, y in zip(a, b))


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


@dataclass
class WebChannel:
    searxng_host: str
    sqlite: "SQLiteStore"
    dense_embedder: "DenseEmbedderPort"
    daily_quota: DailyQuota
    health: WebChannelHealth = field(default_factory=WebChannelHealth)
    sanitization_mode: str = "strict"
    search_top_k: int = 5
    gpu_mutex: object = None
    playwright_priority: int = 3

    def __post_init__(self) -> None:
        self._searxng = SearXNGSearch(host=self.searxng_host)

    async def search_and_extract(
        self,
        query: str,
        sensitive_entities: list[str] | None = None,
        intent: str = "lookup",
    ) -> list[Parent]:
        """
        Full web retrieval pipeline: sanitize → quota → search → extract → embed → Parents.
        Returns empty list on quota exceeded, health outage, or sanitization block.
        """
        if not self.health.is_available:
            log.info("web_channel_skipped_outage")
            return []

        # Quota gate
        if not self.daily_quota.check_and_increment(self.sqlite):
            log.info("web_channel_quota_exceeded")
            return []

        # Sanitization gate
        san = sanitize_query(query, sensitive_entities, mode=self.sanitization_mode)
        if not san.is_safe:
            log.warning("web_sanitization_blocked", patterns=san.blocked_patterns[:3])
            return []

        safe_query = san.sanitized_query
        fresh_mode = intent == "fresh"

        try:
            search_results = await self._searxng.search(
                safe_query,
                top_k=self.search_top_k,
                sqlite=self.sqlite,
                fresh_mode=fresh_mode,
            )
        except Exception as exc:
            self.health.record_failure()
            log.warning("web_search_failed", error=str(exc))
            return []

        if not search_results:
            self.health.record_success()
            return []

        # Extract pages concurrently
        import asyncio
        extractions = await asyncio.gather(
            *[
                extract_page(
                    r.url,
                    snippet=r.snippet,
                    sqlite=self.sqlite,
                    gpu_mutex=self.gpu_mutex,
                    playwright_priority=self.playwright_priority,
                )
                for r in search_results
            ],
            return_exceptions=True,
        )

        # Build (result, extraction) pairs, dropping failures
        valid: list[tuple[WebSearchResult, object]] = [
            (sr, ex)
            for sr, ex in zip(search_results, extractions)
            if not isinstance(ex, Exception) and ex.content_md
        ]
        if not valid:
            self.health.record_success()
            return []

        # Embed all passages in a single batch call
        texts = [ex.content_md[:4096] for _, ex in valid]
        try:
            passage_vecs = await self.dense_embedder.embed_passages(texts)
            query_vec = await self.dense_embedder.embed_query(safe_query, channel=_WEB_CHANNEL)
        except Exception as exc:
            self.health.record_failure()
            log.warning("web_embed_failed", error=str(exc))
            return []

        self.health.record_success()

        parents: list[Parent] = []
        for (sr, ex), pvec in zip(valid, passage_vecs):
            score = _dot_product(list(query_vec), list(pvec))
            uid = _url_hash(sr.url)
            parents.append(Parent(
                parent_id=f"web:{uid}",
                content=f"{ex.content_md}\n\n[external, fetched: {ex.fetched_at}]",
                score=score,
                source_meta=SourceMeta(
                    source_type=_WEB_SOURCE_TYPE,
                    source_id=sr.url,
                    vault_id=_WEB_VAULT_ID,
                    trust_tier=_TRUST_TIER,
                    channel=_WEB_CHANNEL,
                ),
                child_matches=[
                    ChildMatch(chunk_id=f"web:{uid}", score=score, content=ex.content_md[:500])
                ],
                embedding=list(pvec),
            ))

        log.info("web_channel_done", query=safe_query[:60], results=len(parents))
        return parents
