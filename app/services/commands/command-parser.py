"""Slash command parser: extracts /commands from user query text."""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

# Load types directly from sibling file to avoid circular import through __init__.
# Guard against double-loading when __init__ has already registered the module.
_TYPES_KEY = "app.services.commands._types"
if _TYPES_KEY not in sys.modules:
    _types_path = Path(__file__).parent / "command-types.py"
    _types_spec = importlib.util.spec_from_file_location(_TYPES_KEY, _types_path)
    _types_mod = importlib.util.module_from_spec(_types_spec)  # type: ignore[arg-type]
    sys.modules[_TYPES_KEY] = _types_mod  # required before exec so @dataclass resolves __module__
    _types_spec.loader.exec_module(_types_mod)  # type: ignore[union-attr]
ParsedCommand = sys.modules[_TYPES_KEY].ParsedCommand
KNOWN_COMMANDS: frozenset[str] = sys.modules[_TYPES_KEY].KNOWN_COMMANDS

# Matches /name or /name:arg1,arg2
_CMD_RE = re.compile(r"/([a-z][\w-]*)(?::([^\s]+))?", re.UNICODE)


def parse_commands(text: str) -> tuple[str, list]:
    """Extract slash commands from query text.

    Returns (clean_query, commands) where clean_query has all /commands removed.
    Unknown commands are left in text untouched.
    """
    commands: list = []
    spans_to_remove: list[tuple[int, int]] = []

    for m in _CMD_RE.finditer(text):
        name = m.group(1)
        if name not in KNOWN_COMMANDS:
            continue

        raw_args = m.group(2) or ""
        args = [a.strip() for a in raw_args.split(",") if a.strip()] if raw_args else []
        commands.append(ParsedCommand(name=name, args=args))
        spans_to_remove.append((m.start(), m.end()))

    # Remove matched spans right-to-left to preserve offsets
    clean = text
    for start, end in reversed(spans_to_remove):
        clean = clean[:start] + clean[end:]

    clean = re.sub(r"\s{2,}", " ", clean).strip()
    return clean, commands
