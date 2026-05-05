"""Wiki-link extractor and vault-scoped resolver for Tier-0 graph extraction."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote as _pct

# [[Target]] or [[Target|Alias]] or [[Target#heading|Alias]]
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]+)?\]\]")


@dataclass
class ResolvedLink:
    raw_target: str        # original text inside [[...]]
    resolved_uri: str      # urn:note:<vault_id>:<rel_path>, empty if unresolved
    is_stub: bool = False  # True if target note doesn't exist in vault


def extract_wikilinks(content: str) -> list[str]:
    """Return list of raw wikilink targets (before resolution)."""
    return [m.group(1).strip() for m in _WIKILINK_RE.finditer(content)]


def build_notes_index(vault_path: str, vault_id: str) -> dict[str, str]:
    """Scan vault directory → {lowercase_stem_or_alias: note_uri} mapping.

    Case-insensitive, alias-aware. Last-writer wins for duplicate stems.
    """
    import importlib.util as _ilu
    import sys as _sys

    # Load frontmatter-parser without adding to sys.path (kebab name); cache in sys.modules
    if "_fp" not in _sys.modules:
        _here = Path(__file__).parent
        _spec = _ilu.spec_from_file_location("_fp", _here / "frontmatter-parser.py")
        _mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
        _sys.modules["_fp"] = _mod  # must be registered before exec so @dataclass resolves __module__
        _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
    _parse_fm = _sys.modules["_fp"].parse_frontmatter

    index: dict[str, str] = {}
    vault = Path(vault_path)
    if not vault.exists():
        return index

    for md_file in vault.rglob("*.md"):
        try:
            rel = md_file.relative_to(vault)
            note_uri = f"urn:note:{vault_id}:{_pct(str(rel), safe='/:.-_')}"
            stem = md_file.stem.lower()
            index[stem] = note_uri
            index[str(rel).lower()] = note_uri

            # Also index aliases from frontmatter
            try:
                raw = md_file.read_text(encoding="utf-8", errors="replace")
                fm = _parse_fm(raw)
                for alias in fm.aliases:
                    index[alias.lower()] = note_uri
            except Exception:
                pass
        except Exception:
            continue

    return index


def resolve_wikilinks(
    targets: list[str],
    vault_id: str,
    notes_index: dict[str, str],
) -> list[ResolvedLink]:
    """Resolve wikilink targets to note URIs using vault-scoped index."""
    results: list[ResolvedLink] = []
    for raw in targets:
        target_lower = raw.strip().lower()
        # Try exact stem match, then with .md extension
        uri = notes_index.get(target_lower) or notes_index.get(target_lower + ".md")
        if uri:
            results.append(ResolvedLink(raw_target=raw, resolved_uri=uri, is_stub=False))
        else:
            # Unresolved: emit stub node (pkm:linksTo target that doesn't exist yet)
            stub_uri = f"urn:wikilink-stub:{vault_id}:{_slugify(raw)}"
            results.append(ResolvedLink(raw_target=raw, resolved_uri=stub_uri, is_stub=True))
    return results


def _slugify(text: str) -> str:
    return re.sub(r"[^\wЀ-ӿ\-]", "-", text.lower().strip()).strip("-") or "empty"
