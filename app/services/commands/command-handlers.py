"""Slash command handlers — mutate RetrievalContext per parsed command."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.commands import ParsedCommand
    from app.services.router import RetrievalContext


def apply_commands(ctx: "RetrievalContext", commands: "list[ParsedCommand]") -> "RetrievalContext":
    """Apply all parsed commands to context in order. Returns mutated ctx."""
    for cmd in commands:
        match cmd.name:
            case "scope":
                # /scope:vault1,vault2 — restrict retrieval to listed vaults
                if cmd.args:
                    ctx.vault_scope = cmd.args
            case "web":
                ctx.force_web = True
                ctx.no_web = False
            case "no-web":
                ctx.force_web = False
                ctx.no_web = True
            case "strict":
                ctx.strict_mode = True
            case "explain":
                ctx.explain_mode = True
            case "no-check":
                ctx.skip_faithfulness = True
            case "deep":
                ctx.deep_mode = True
            case "explore":
                ctx.explore_mode = True
            case "sources":
                ctx.sources_only = True
                # /sources:notes,books — restrict to listed channels
                if cmd.args:
                    ctx.sources_filter = cmd.args
            case "save-web":
                # URL in args (/save-web:https://...) or in ctx.query remainder
                if cmd.args:
                    ctx.save_web_url = cmd.args[0]
                elif ctx.query.strip().startswith("http"):
                    ctx.save_web_url = ctx.query.strip()
    return ctx
