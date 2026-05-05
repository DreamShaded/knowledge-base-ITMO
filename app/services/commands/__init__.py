"""Commands package: slash command parsing and RetrievalContext mutation."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_DIR = Path(__file__).parent
_PKG = "app.services.commands"


def _load(logical: str, filename: str):
    """Load a kebab-case module file and register it in sys.modules."""
    fqname = f"{_PKG}.{logical}"
    spec = importlib.util.spec_from_file_location(fqname, _DIR / filename)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[fqname] = mod  # required before exec so @dataclass resolves __module__
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_types_mod = _load("_types", "command-types.py")
_parser_mod = _load("_parser", "command-parser.py")
_handlers_mod = _load("_handlers", "command-handlers.py")

ParsedCommand = _types_mod.ParsedCommand
KNOWN_COMMANDS = _types_mod.KNOWN_COMMANDS
parse_commands = _parser_mod.parse_commands
apply_commands = _handlers_mod.apply_commands

__all__ = [
    "ParsedCommand",
    "KNOWN_COMMANDS",
    "parse_commands",
    "apply_commands",
]
