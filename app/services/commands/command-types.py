"""Slash command type definitions."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ParsedCommand:
    name: str        # e.g. "scope", "web", "strict"
    args: list[str] = field(default_factory=list)  # e.g. ["personal", "work"] for /scope


# Commands recognized by the parser (phase-08 adds handlers for all)
KNOWN_COMMANDS: frozenset[str] = frozenset([
    "scope",      # /scope:vault1,vault2 — restrict to specific vaults
    "web",        # force web channel ON
    "no-web",     # force web channel OFF
    "strict",     # disable Z3 (no general knowledge fallback)
    "explain",    # explanation mode (more verbose Z2)
    "no-check",   # skip faithfulness check (phase-09)
    "deep",       # deeper retrieval (higher top-k)
    "explore",    # exploration mode (MMR diversity boost)
    "save-web",   # save web results to vault (phase-07)
    "sources",    # return sources list only, no generation
])
