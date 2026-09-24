"""Multi-host config presets, Competitive Response Plan Step 7.

Each host's snippet shape was verified against that host's own docs on
2026-08-09 (see cairn/interface/commands/mcp.py comments for sources) -
these tests pin the verified shape down so it can't silently regress.
"""
from __future__ import annotations

import json

from typer.testing import CliRunner

from cairn.interface.cli import app

runner = CliRunner()


def _run(host: str) -> str:
    result = runner.invoke(app, ["mcp", "config", "--host", host])
    assert result.exit_code == 0, result.exception
    return result.stdout


def _extract_json(stdout: str) -> dict:
    start = stdout.index("{")
    end = stdout.rindex("}") + 1
    return json.loads(stdout[start:end])


def test_zed_uses_context_servers_key_not_mcp_servers():
    data = _extract_json(_run("zed"))
    assert "context_servers" in data
    assert "mcpServers" not in data
    assert data["context_servers"]["cairn"]["command"] == "cairn"
    assert data["context_servers"]["cairn"]["args"] == ["mcp", "serve"]


def test_opencode_uses_mcp_key_with_type_local_and_combined_command_array():
    data = _extract_json(_run("opencode"))
    assert "mcp" in data
    entry = data["mcp"]["cairn"]
    assert entry["type"] == "local"
    assert entry["command"] == ["cairn", "mcp", "serve"]
    assert "args" not in entry  # OpenCode combines command+args into one array


def test_windsurf_uses_standard_mcp_servers_shape():
    data = _extract_json(_run("windsurf"))
    assert data["mcpServers"]["cairn"]["command"] == "cairn"


def test_all_hosts_produce_output_without_crashing():
    from cairn.interface.commands.mcp import _ALL_HOSTS
    for host in _ALL_HOSTS:
        result = runner.invoke(app, ["mcp", "config", "--host", host])
        assert result.exit_code == 0, f"{host}: {result.exception}"
        assert len(result.stdout) > 0
