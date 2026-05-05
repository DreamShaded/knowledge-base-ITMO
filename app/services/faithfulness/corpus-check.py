"""Step 2: Partial → corpus check — upgrade partial verdicts via additional retrieval (ADR-0007).

Inject retrieval_fn to enable; without it partial verdicts stay partial⚠.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from app.logging import get_logger
from app.services.faithfulness.schemas import ClaimVerdict

log = get_logger(__name__)

RetrievalFn = Callable[[str], Awaitable[list[str]]]


async def corpus_check(
    verdicts: list[ClaimVerdict],
    retrieval_fn: RetrievalFn | None = None,
) -> list[ClaimVerdict]:
    """Upgrade 'partial' verdicts to 'full' if corpus evidence found.

    Without retrieval_fn, partial verdicts stay partial⚠ (no-op path).
    """
    if retrieval_fn is None:
        return verdicts

    updated: list[ClaimVerdict] = []
    for v in verdicts:
        if v.verdict != "partial":
            updated.append(v)
            continue
        try:
            snippets = await retrieval_fn(v.claim.text)
            if snippets:
                log.debug("corpus_upgrade", claim=v.claim.text[:60])
                updated.append(v.model_copy(update={"verdict": "full", "warning": False}))
            else:
                updated.append(v)
        except Exception as e:
            log.warning("corpus_check_error", error=str(e))
            updated.append(v)

    return updated
