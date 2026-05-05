"""Unit tests for slash command parser (ADR-0004 §8)."""
import importlib.util
import sys
from pathlib import Path

import pytest

_path = Path(__file__).parent.parent.parent / "app/services/commands/command-parser.py"
_spec = importlib.util.spec_from_file_location("app.services.commands._parser_test", _path)
_mod = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules["app.services.commands._parser_test"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]

parse_commands = _mod.parse_commands


class TestBasicParsing:
    def test_single_flag_command(self):
        clean, cmds = parse_commands("explain quantum entanglement /strict")
        assert clean == "explain quantum entanglement"
        assert len(cmds) == 1
        assert cmds[0].name == "strict"
        assert cmds[0].args == []

    def test_command_with_args(self):
        clean, cmds = parse_commands("/scope:personal,work найди заметки о docker")
        assert "найди заметки о docker" in clean
        assert len(cmds) == 1
        assert cmds[0].name == "scope"
        assert cmds[0].args == ["personal", "work"]

    def test_web_command(self):
        clean, cmds = parse_commands("last news about LLMs /web")
        assert clean == "last news about LLMs"
        assert any(c.name == "web" for c in cmds)

    def test_no_commands(self):
        clean, cmds = parse_commands("what is machine learning")
        assert clean == "what is machine learning"
        assert cmds == []


class TestMultipleCommands:
    def test_two_commands(self):
        clean, cmds = parse_commands("объясни CQRS /strict /explain")
        assert "объясни CQRS" in clean
        names = [c.name for c in cmds]
        assert "strict" in names
        assert "explain" in names

    def test_scope_and_deep(self):
        _, cmds = parse_commands("/scope:books /deep найди про event sourcing")
        names = [c.name for c in cmds]
        assert "scope" in names
        assert "deep" in names
        scope_cmd = next(c for c in cmds if c.name == "scope")
        assert scope_cmd.args == ["books"]


class TestEdgeCases:
    def test_unknown_command_left_in_text(self):
        clean, cmds = parse_commands("query text /unknowncmd more text")
        assert "/unknowncmd" in clean
        assert cmds == []

    def test_command_at_start(self):
        clean, cmds = parse_commands("/web найди новости про Python")
        assert "найди новости про Python" in clean
        assert cmds[0].name == "web"

    def test_command_in_middle(self):
        clean, cmds = parse_commands("найди /strict заметки про docker")
        assert "/strict" not in clean
        assert cmds[0].name == "strict"

    def test_empty_query_after_commands(self):
        clean, cmds = parse_commands("/strict /web")
        assert clean == ""
        assert len(cmds) == 2

    def test_no_web_command(self):
        _, cmds = parse_commands("query /no-web")
        assert cmds[0].name == "no-web"

    def test_scope_single_vault(self):
        _, cmds = parse_commands("/scope:personal my notes on docker")
        scope = next(c for c in cmds if c.name == "scope")
        assert scope.args == ["personal"]

    def test_scope_three_vaults(self):
        _, cmds = parse_commands("/scope:a,b,c query")
        scope = next(c for c in cmds if c.name == "scope")
        assert scope.args == ["a", "b", "c"]

    def test_sources_command(self):
        _, cmds = parse_commands("what sources do you have /sources")
        assert any(c.name == "sources" for c in cmds)

    def test_no_check_command(self):
        _, cmds = parse_commands("tell me /no-check about reactivity")
        assert any(c.name == "no-check" for c in cmds)

    def test_whitespace_collapsed(self):
        clean, _ = parse_commands("  query   /strict   more text  ")
        assert "  " not in clean

    def test_hyphenated_command(self):
        _, cmds = parse_commands("save this /save-web")
        assert any(c.name == "save-web" for c in cmds)
