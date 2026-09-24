"""`cairn mcp config`, tool list must be derived, not hardcoded; host formats must be valid."""
from __future__ import annotations

from typer.testing import CliRunner

from cairn.interface.cli import app
from cairn.interface.commands.mcp import _tool_names
from cairn.mcp.server import CairnMCPServer

runner = CliRunner()


def test_tool_names_matches_live_schema():
    names = _tool_names()
    expected = {t["name"] for t in CairnMCPServer()._tools}
    assert set(names) == expected
    assert len(names) == len(CairnMCPServer()._tools)


def test_config_output_lists_every_tool():
    result = runner.invoke(app, ["mcp", "config", "--host", "claude-code"])
    assert result.exit_code == 0
    for name in _tool_names():
        assert name in result.stdout


def test_config_claude_code_does_not_print_desktop_path():
    result = runner.invoke(app, ["mcp", "config", "--host", "claude-code"])
    assert result.exit_code == 0
    assert "claude_desktop_config.json" not in result.stdout


def test_config_codex_emits_valid_toml_not_json():
    result = runner.invoke(app, ["mcp", "config", "--host", "codex"])
    assert result.exit_code == 0
    assert "[mcp_servers.cairn]" in result.stdout
    assert 'command = "' in result.stdout
    assert "{" not in result.stdout.split("[mcp_servers.cairn]")[1].split("\n\n")[0]


def test_config_generic_still_prints_json():
    result = runner.invoke(app, ["mcp", "config", "--host", "cursor"])
    assert result.exit_code == 0
    assert '"mcpServers"' in result.stdout
