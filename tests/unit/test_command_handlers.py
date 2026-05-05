"""Unit tests for apply_commands slash command handlers (ADR-0004 §8, phase-08)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ── Load modules via importlib (kebab-case filenames) ─────────────────────────

_ROOT = Path(__file__).parent.parent.parent / "app"


def _load(logical: str, rel_path: str):
    fqname = f"_test_handlers.{logical}"
    if fqname in sys.modules:
        return sys.modules[fqname]
    spec = importlib.util.spec_from_file_location(fqname, _ROOT / rel_path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[fqname] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_types_mod = _load("types", "services/commands/command-types.py")
ParsedCommand = _types_mod.ParsedCommand

# Load router-decision for RetrievalContext
_decision_mod = _load("decision", "services/router/router-decision.py")
RetrievalContext = _decision_mod.RetrievalContext

# Load handlers
_handlers_mod = _load("handlers", "services/commands/command-handlers.py")
apply_commands = _handlers_mod.apply_commands


def _ctx(query: str = "test query") -> RetrievalContext:
    return RetrievalContext(query=query)


def _cmd(name: str, args: list[str] | None = None) -> ParsedCommand:
    return ParsedCommand(name=name, args=args or [])


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestSourcesCommand:
    def test_sources_sets_sources_only(self):
        ctx = apply_commands(_ctx(), [_cmd("sources")])
        assert ctx.sources_only is True

    def test_sources_no_args_no_filter(self):
        ctx = apply_commands(_ctx(), [_cmd("sources")])
        assert ctx.sources_filter == []

    def test_sources_with_channel_args(self):
        ctx = apply_commands(_ctx(), [_cmd("sources", ["notes", "books"])])
        assert ctx.sources_only is True
        assert ctx.sources_filter == ["notes", "books"]

    def test_sources_single_channel(self):
        ctx = apply_commands(_ctx(), [_cmd("sources", ["notes"])])
        assert ctx.sources_filter == ["notes"]


class TestScopeCommand:
    def test_scope_sets_vault_scope(self):
        ctx = apply_commands(_ctx(), [_cmd("scope", ["personal"])])
        assert ctx.vault_scope == ["personal"]

    def test_scope_multiple_vaults(self):
        ctx = apply_commands(_ctx(), [_cmd("scope", ["personal", "work"])])
        assert ctx.vault_scope == ["personal", "work"]

    def test_scope_empty_args_no_change(self):
        ctx = _ctx()
        ctx.vault_scope = "all"
        apply_commands(ctx, [_cmd("scope", [])])
        assert ctx.vault_scope == "all"


class TestWebCommands:
    def test_web_sets_force_web(self):
        ctx = apply_commands(_ctx(), [_cmd("web")])
        assert ctx.force_web is True
        assert ctx.no_web is False

    def test_no_web_sets_no_web(self):
        ctx = apply_commands(_ctx(), [_cmd("no-web")])
        assert ctx.no_web is True
        assert ctx.force_web is False

    def test_web_clears_no_web(self):
        ctx = _ctx()
        ctx.no_web = True
        apply_commands(ctx, [_cmd("web")])
        assert ctx.no_web is False


class TestFlagCommands:
    def test_strict_mode(self):
        ctx = apply_commands(_ctx(), [_cmd("strict")])
        assert ctx.strict_mode is True

    def test_explain_mode(self):
        ctx = apply_commands(_ctx(), [_cmd("explain")])
        assert ctx.explain_mode is True

    def test_no_check(self):
        ctx = apply_commands(_ctx(), [_cmd("no-check")])
        assert ctx.skip_faithfulness is True

    def test_deep_mode(self):
        ctx = apply_commands(_ctx(), [_cmd("deep")])
        assert ctx.deep_mode is True

    def test_explore_mode(self):
        ctx = apply_commands(_ctx(), [_cmd("explore")])
        assert ctx.explore_mode is True


class TestSaveWebCommand:
    def test_save_web_with_url_arg(self):
        ctx = apply_commands(_ctx(), [_cmd("save-web", ["https://example.com"])])
        assert ctx.save_web_url == "https://example.com"

    def test_save_web_url_from_query(self):
        ctx = _ctx(query="https://example.org/article")
        apply_commands(ctx, [_cmd("save-web", [])])
        assert ctx.save_web_url == "https://example.org/article"

    def test_save_web_no_url_no_change(self):
        ctx = _ctx(query="just text no url")
        apply_commands(ctx, [_cmd("save-web", [])])
        assert ctx.save_web_url == ""


class TestMultipleCommands:
    def test_strict_and_explain(self):
        ctx = apply_commands(_ctx(), [_cmd("strict"), _cmd("explain")])
        assert ctx.strict_mode is True
        assert ctx.explain_mode is True

    def test_sources_and_scope(self):
        ctx = apply_commands(_ctx(), [_cmd("sources", ["notes"]), _cmd("scope", ["personal"])])
        assert ctx.sources_only is True
        assert ctx.sources_filter == ["notes"]
        assert ctx.vault_scope == ["personal"]

    def test_all_flags(self):
        cmds = [
            _cmd("strict"), _cmd("explain"), _cmd("no-check"),
            _cmd("deep"), _cmd("explore"), _cmd("web"),
        ]
        ctx = apply_commands(_ctx(), cmds)
        assert ctx.strict_mode
        assert ctx.explain_mode
        assert ctx.skip_faithfulness
        assert ctx.deep_mode
        assert ctx.explore_mode
        assert ctx.force_web
