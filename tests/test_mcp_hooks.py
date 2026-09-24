"""cairn mcp config --with-hooks, Competitive Response Plan Step 6."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from cairn.interface.cli import app

runner = CliRunner()


def test_without_flag_no_hook_output():
    result = runner.invoke(app, ["mcp", "config", "--host", "claude-code"])
    assert result.exit_code == 0
    assert "SessionStart" not in result.stdout


def test_claude_code_hook_uses_command_type_not_mcp_tool():
    result = runner.invoke(app, ["mcp", "config", "--host", "claude-code", "--with-hooks"])
    assert result.exit_code == 0
    assert "SessionStart" in result.stdout
    assert '"type": "command"' in result.stdout
    assert '"type": "mcp_tool"' not in result.stdout
    assert "memory boot" in result.stdout


def test_claude_code_hook_is_valid_json():
    result = runner.invoke(app, ["mcp", "config", "--host", "claude-code", "--with-hooks"])
    # extract the hook JSON block (starts at the second '{' after "Optional:")
    marker = "Optional: SessionStart hook"
    idx = result.stdout.index(marker)
    tail = result.stdout[idx:]
    json_start = tail.index("{")
    parsed = json.loads(tail[json_start:])
    assert parsed["hooks"]["SessionStart"][0]["hooks"][0]["type"] == "command"


def test_unverified_host_says_so_instead_of_guessing():
    result = runner.invoke(app, ["mcp", "config", "--host", "cursor", "--with-hooks"])
    assert result.exit_code == 0
    assert "No verified SessionStart-equivalent hook recipe" in result.stdout


def test_hook_flag_never_writes_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.rglob("*"))
    runner.invoke(app, ["mcp", "config", "--host", "claude-code", "--with-hooks"])
    after = set(tmp_path.rglob("*"))
    assert before == after
