"""Step 4 (optional): Cross-source check for negation claims (ADR-0007).

Only activates for claim_type='negation'. factual_specific deferred until eval data.
Enable via pipeline enable_cross_source=True flag.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from app.logging import get_logger
from app.services.faithfulness.schemas import ClaimVerdict

log = get_logger(__name__)

RetrievalFn = Callable[[str], Awaitable[list[str]]]

_MIN_CONFIRMING = 2  # ≥2 sources needed to upgrade negation claim to full


async def cross_source_check(
    verdicts: list[ClaimVerdict],
    retrieval_fn: RetrievalFn | None = None,
) -> list[ClaimVerdict]:
    """Cross-source check for negation claims with warning verdicts.

    If ≥_MIN_CONFIRMING sources confirm → upgrade to full.
    If fewer sources found → note possible contradiction in reasoning.
    """
    if retrieval_fn is None:
        return verdicts

    updated: list[ClaimVerdict] = []
    for v in verdicts:
        if v.claim.claim_type != "negation" or v.verdict not in ("none", "partial"):
            updated.append(v)
            continue
        try:
            snippets = await retrieval_fn(v.claim.text)
            if len(snippets) >= _MIN_CONFIRMING:
                log.debug("cross_source_confirmed", claim=v.claim.text[:60])
                updated.append(v.model_copy(update={"verdict": "full", "warning": False}))
            elif snippets:
                note = " [cross-source: contradiction possible]"
                updated.append(v.model_copy(update={
                    "reasoning_short": v.reasoning_short + note,
                }))
            else:
                updated.append(v)
        except Exception as e:
            log.warning("cross_source_error", error=str(e))
            updated.append(v)

    return updated
