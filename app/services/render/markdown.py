"""MarkdownRenderer: build full structured Markdown from AnswerEnvelope (ADR-0006)."""
from __future__ import annotations

import re
import urllib.parse
from typing import TYPE_CHECKING

from app.services.render.verbatim_quote import extract_verbatim

if TYPE_CHECKING:
    from app.services.faithfulness.schemas import FaithfulnessResult
    from app.services.generator.answer_envelope import AnswerEnvelope, Citation


# ── Link builders ─────────────────────────────────────────────────────────────

def _note_links(
    citation: "Citation",
    vault_map: dict[str, str],
    base_url: str,
) -> tuple[str, str]:
    """Return (primary_url, preview_url) for a note citation."""
    sid = citation.source_id  # e.g. "personal:folder/note.md" or "folder/note.md"
    # Strip vault_id prefix (format: "vault_id:rel_path")
    rel = sid.split(":", 1)[1] if ":" in sid else sid
    encoded = urllib.parse.quote(rel, safe="")
    vault_id = citation.vault_id

    # Obsidian deep-link (primary)
    uri_name = vault_map.get(vault_id, vault_id)
    if uri_name:
        primary = f"obsidian://open?vault={urllib.parse.quote(uri_name)}&file={encoded}"
    else:
        primary = ""

    # HTTP preview (secondary) — encode spaces/unicode but keep slashes as path separators
    rel_encoded = urllib.parse.quote(rel, safe="/")
    preview = f"{base_url}/preview/note/{vault_id}/{rel_encoded}"
    return primary, preview


def _book_links(citation: "Citation", base_url: str) -> tuple[str, str]:
    """Return (primary_url, preview_url) for a book_chapter citation.

    Book chapters are preview-only; primary falls back to preview.
    source_id format: "<sha>/<chapter_id>"
    """
    parts = citation.source_id.split("/", 1)
    sha = parts[0] if parts else citation.source_id
    chapter_id = parts[1] if len(parts) > 1 else "0"
    preview = f"{base_url}/preview/book/{sha}/{chapter_id}"
    return preview, preview  # no separate primary for books


def _web_saved_links(citation: "Citation", base_url: str) -> tuple[str, str]:
    """Return (primary_url, preview_url) for web_saved citation."""
    sha = citation.source_id
    preview = f"{base_url}/preview/web/{sha}"
    return preview, preview


def _web_links(citation: "Citation") -> tuple[str, str]:
    """Return (primary_url, '') for live web citation."""
    return citation.source_id, ""


def _wikidata_links(citation: "Citation") -> tuple[str, str]:
    """Return (primary_url, '') for wikidata citation."""
    qid = citation.source_id.lstrip("wd:")
    return f"https://www.wikidata.org/wiki/{qid}", ""


def _resolve_links(
    citation: "Citation",
    vault_map: dict[str, str],
    base_url: str,
) -> tuple[str, str]:
    """Dispatch to correct link builder based on source_type."""
    st = citation.source_type
    if st == "note":
        return _note_links(citation, vault_map, base_url)
    if st == "book_chapter":
        return _book_links(citation, base_url)
    if st == "web_saved":
        return _web_saved_links(citation, base_url)
    if st == "web":
        return _web_links(citation)
    if st == "wikidata":
        return _wikidata_links(citation)
    # Unknown type: best-effort
    return citation.source_id, ""


# ── Footnote builder ──────────────────────────────────────────────────────────

def _footnote(citation: "Citation", primary: str, preview: str, query: str) -> str:
    """Build a single footnote block per ADR-0006 format."""
    lines: list[str] = []

    title = citation.title or citation.source_id
    if primary:
        heading_link = f"**[{title}]({primary})**"
    else:
        heading_link = f"**{title}**"

    if preview and preview != primary:
        first_line = f"[^{citation.id}]: {heading_link} · [preview]({preview})"
    else:
        first_line = f"[^{citation.id}]: {heading_link}"
    lines.append(first_line)

    # Metadata line
    meta_parts: list[str] = []
    if citation.modified_at:
        meta_parts.append(citation.modified_at)
    score_val = getattr(citation, "score", 0.0)
    if score_val:
        meta_parts.append(f"score {score_val:.2f}")
    if meta_parts:
        lines.append(f"    *{' · '.join(meta_parts)}*")

    # Verbatim quote
    snippet = citation.snippet or ""
    if snippet:
        quote = extract_verbatim(snippet, query)
        if quote:
            lines.append(f"    > {quote}")

    return "\n".join(lines)


# ── Faithfulness warning helpers ──────────────────────────────────────────────

_AGGREGATE_WARNING = "> ⚠ **Ответ может содержать неподтверждённые утверждения** — часть фактических утверждений не покрыта источниками."
_CITE_RE = re.compile(r"\[\^(\d+)\]")


def apply_claim_warnings(content: str, warning_refs: set[int]) -> str:
    """Add ⚠ after [^N] citation markers for uncovered claims. Exposed for testing."""
    if not warning_refs:
        return content

    def _replace(m: re.Match) -> str:
        ref = int(m.group(1))
        return f"[^{ref}] ⚠" if ref in warning_refs else m.group(0)

    # Only replace refs not already followed by ⚠
    return re.sub(r"\[\^(\d+)\](?! ⚠)", _replace, content)


def _warning_refs_from(faithfulness: "FaithfulnessResult") -> set[int]:
    """Collect citation ref numbers that belong to warning verdicts."""
    refs: set[int] = set()
    for v in faithfulness.verdicts:
        if v.warning:
            refs.update(v.claim.citation_refs)
    return refs


# ── MarkdownRenderer ──────────────────────────────────────────────────────────

class MarkdownRenderer:
    """Render an AnswerEnvelope to structured Markdown."""

    def render(
        self,
        envelope: "AnswerEnvelope",
        *,
        vault_map: dict[str, str] | None = None,
        base_url: str = "http://localhost:8000",
        query: str = "",
        faithfulness: "FaithfulnessResult | None" = None,
    ) -> str:
        """Build full Markdown string from envelope.

        Args:
            envelope:      The answer to render.
            vault_map:     {vault_id: obsidian_uri_name} for deep-link generation.
            base_url:      Base URL for preview routes.
            query:         Original user query (used for verbatim quote extraction).
            faithfulness:  Optional FaithfulnessResult; adds ⚠ warnings when present.
        """
        if vault_map is None:
            vault_map = {}

        if envelope.refusal:
            return self._render_refusal(envelope, query)

        parts: list[str] = []

        # TL;DR
        if envelope.tldr:
            parts.append(f"## TL;DR\n{envelope.tldr}\n")

        # Faithfulness: collect warning refs and aggregate flag
        warn_refs: set[int] = set()
        show_aggregate = False
        if faithfulness and not faithfulness.skipped:
            warn_refs = _warning_refs_from(faithfulness)
            show_aggregate = faithfulness.aggregate_warning

        # Sections: Z1+Z2 under "Ответ", Z3 separate
        z1z2 = [s for s in envelope.sections if s.zone in ("Z1", "Z2")]
        z3 = [s for s in envelope.sections if s.zone == "Z3"]

        if z1z2:
            if show_aggregate:
                parts.append(_AGGREGATE_WARNING)
            parts.append("## Ответ")
            for s in z1z2:
                if s.heading:
                    parts.append(f"\n### {s.heading}\n")
                content = apply_claim_warnings(s.content, warn_refs) if s.zone == "Z1" else s.content
                parts.append(content)

        if z3:
            parts.append("\n## Из общих знаний (не из источников)")
            for s in z3:
                if s.heading:
                    parts.append(f"\n### {s.heading}\n")
                parts.append(s.content)

        # Related materials
        if envelope.related:
            parts.append("\n## Связанные материалы")
            for item in envelope.related:
                parts.append(f"- {item}")

        # Footnotes / sources
        if envelope.citations:
            parts.append("\n## Источники")
            for c in envelope.citations:
                primary, preview = _resolve_links(c, vault_map, base_url)
                parts.append(_footnote(c, primary, preview, query))

        # Confidence
        cs = envelope.confidence_signal
        parts.append("\n## Уверенность")
        parts.append(f"**{cs.level}** — {cs.reason}" if cs.reason else f"**{cs.level}**")

        return "\n".join(parts)

    # ── Refusal ───────────────────────────────────────────────────────────────

    def _render_refusal(self, envelope: "AnswerEnvelope", query: str) -> str:
        query_display = query.strip() or "…"
        lines = [
            "## Не нашёл в источниках",
            f'По запросу «{query_display}» в твоих заметках, книгах и '
            "веб-результатах ничего достаточно релевантного не нашлось.",
            "",
            "Попробуй переформулировать или используй `/explain` для ответа из общих знаний.",
        ]
        if envelope.refusal_message:
            lines.insert(1, envelope.refusal_message)
        return "\n".join(lines)
