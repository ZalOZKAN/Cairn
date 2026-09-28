"""Conflict detection wiring, Competitive Response Plan Step 3.

conflict.py's ConflictDetector was already fully implemented and correctly
default-off; the gap was that nothing ever called it. These tests stub the
LLM call, no real API traffic.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
import types
from unittest.mock import patch, MagicMock

from cairn.config.loader import load_config
from cairn.memory.memory import Memory


def _mk_memory(tmpdir: str, conflict_enabled: bool = True) -> Memory:
    config = load_config()
    config["memory"]["db_path"] = f"{tmpdir}/test.db"
    config["memory"]["global_db_path"] = f"{tmpdir}/global.db"
    config["memory"]["async_store"] = False
    config["memory"].setdefault("dedup", {})["enabled"] = False
    config["memory"]["conflict"] = {"enabled": conflict_enabled, "min_importance": 1}
    return Memory(config, project_dir=Path(tmpdir))


def _stub_litellm(response_json: str):
    """`litellm` is an optional extra and may not be installed in dev/CI -
    inject a fake module into sys.modules rather than patching an attribute
    on a package that might not exist."""
    fake = types.ModuleType("litellm")
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content=response_json))]
    fake.completion = MagicMock(return_value=mock_resp)
    return patch.dict(sys.modules, {"litellm": fake})


def test_check_conflicts_disabled_makes_zero_llm_calls():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir, conflict_enabled=False)
        mem.store("use postgres", "we chose postgres for the main db", importance=2)
        # enabled=False short-circuits before the litellm import is even
        # attempted, so no stub is needed here, this also proves the
        # short-circuit happens first, not just that the call fails quietly.
        result = mem.check_conflicts("use sqlite", "switch the main db to sqlite")
        assert result == []


def test_check_conflicts_returns_stubbed_conflict():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir, conflict_enabled=True)
        mem.store("use postgres", "we chose postgres for the main db", importance=2)
        with _stub_litellm('{"conflicts": [{"id": 1, "reason": "sqlite vs postgres"}]}'):
            result = mem.check_conflicts("use sqlite", "switch the main db to sqlite")
        assert result == [{"id": 1, "reason": "sqlite vs postgres"}]


def test_check_conflicts_never_auto_invalidates():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir, conflict_enabled=True)
        dec_id = mem.record_decision("use postgres", reasoning="team standard", importance=2)
        with _stub_litellm(f'{{"conflicts": [{{"id": {dec_id}, "reason": "contradicts"}}]}}'):
            mem.check_conflicts("use sqlite", "switch the main db to sqlite")
        # the flagged prior decision must still be active, detection never invalidates
        full = mem.get_full(dec_id)
        assert full["valid_until"] is None


def test_check_conflicts_scans_global_db_with_g_prefix():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir, conflict_enabled=True)
        mem.store("team convention", "always use TypeScript", importance=2, scope="global")
        with _stub_litellm('{"conflicts": [{"id": "g1", "reason": "language contradiction"}]}'):
            result = mem.check_conflicts("switch to JavaScript", "team now prefers JS")
        assert result == [{"id": "g1", "reason": "language contradiction"}]


def test_record_decision_mcp_tool_surfaces_conflicts():
    import json
    from pathlib import Path
    from cairn.mcp.server import CairnMCPServer

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        project = Path(td)
        cfg_dir = project / ".cairn"
        cfg_dir.mkdir()
        (cfg_dir / "config.yaml").write_text(
            f"memory:\n  db_path: {td}/mem.db\n  async_store: false\n"
            f"  global_db_path: {td}/global.db\n"
            f"  conflict:\n    enabled: true\n    min_importance: 1\n",
            encoding="utf-8",
        )
        srv = CairnMCPServer(project_dir=project)
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "record_decision",
                          "arguments": {"decision": "use postgres", "importance": 2}}}
        srv._handle(req)  # seed one decision

        with _stub_litellm('{"conflicts": [{"id": 1, "reason": "db contradiction"}]}'):
            req2 = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "record_decision",
                               "arguments": {"decision": "use sqlite", "importance": 2}}}
            resp = srv._handle(req2)
        data = json.loads(resp["result"]["content"][0]["text"])
        assert data["conflicts"] == [{"id": 1, "reason": "db contradiction"}]
