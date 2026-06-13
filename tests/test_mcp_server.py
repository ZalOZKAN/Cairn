"""MCP server tool-handler tests, no stdio, just direct dispatch."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from cairn.mcp.server import CairnMCPServer


@pytest.fixture
def srv():
    """An MCP server pointing at an isolated project dir + memory db."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        project = Path(td)
        # Force memory to use a local db inside the temp dir
        cfg_dir = project / ".cairn"
        cfg_dir.mkdir()
        (cfg_dir / "config.yaml").write_text(
            f"memory:\n  db_path: {td}/mem.db\n  async_store: false\n",
            encoding="utf-8",
        )
        s = CairnMCPServer(project_dir=project)
        yield s


# --- tool catalogue -------------------------------------------------------

def test_tools_list_returns_expected_set(srv):
    names = {t["name"] for t in srv._tools}
    expected = {
        "memory_recall", "memory_store", "memory_recent", "memory_search",
        "memory_invalidate", "memory_boot", "memory_status",
        "list_skills", "get_skill",
        "list_workflows", "get_workflow",
        "list_agents", "get_agent",
    }
    assert expected <= names


def test_tools_have_input_schemas(srv):
    for t in srv._tools:
        assert "inputSchema" in t
        assert t["inputSchema"]["type"] == "object"


# --- jsonrpc dispatch ----------------------------------------------------

def test_initialize_returns_server_info(srv):
    req = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    resp = srv._handle(req)
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "cairn"


def test_tools_list_method(srv):
    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    resp = srv._handle(req)
    assert "tools" in resp["result"]
    assert len(resp["result"]["tools"]) >= 13


# --- memory tools end-to-end ---------------------------------------------

def _call(srv, name, args):
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": name, "arguments": args}}
    return srv._handle(req)["result"]["content"][0]["text"]


def test_memory_store_then_recall(srv):
    assert _call(srv, "memory_store",
                 {"prompt": "jwt cookies decision",
                  "response": "use httpOnly cookies for jwt to avoid xss",
                  "importance": 2}) == "stored"
    out = _call(srv, "memory_recall", {"query": "jwt cookies"})
    assert "httpOnly" in out


def test_memory_recent_and_search(srv):
    _call(srv, "memory_store", {"prompt": "alpha topic one", "response": "answer one"})
    _call(srv, "memory_store", {"prompt": "alpha topic two", "response": "answer two"})
    recent = json.loads(_call(srv, "memory_recent", {"n": 5}))
    assert len(recent) == 2
    found = json.loads(_call(srv, "memory_search", {"query": "alpha"}))
    assert len(found) == 2


def test_memory_invalidate_hides_entry(srv):
    _call(srv, "memory_store", {"prompt": "temporary fact", "response": "old answer"})
    recent = json.loads(_call(srv, "memory_recent", {"n": 5}))
    row_id = None
    # need to look up id via direct sqlite, recent doesn't return ids
    import sqlite3
    with sqlite3.connect(srv.project.memory.db_path) as conn:
        row_id = conn.execute("SELECT id FROM memories LIMIT 1").fetchone()[0]
    assert _call(srv, "memory_invalidate", {"id": row_id, "reason": "outdated"}) == "invalidated"
    found = json.loads(_call(srv, "memory_search", {"query": "temporary"}))
    assert found == []


# --- skills/workflows/agents file access ---------------------------------

def test_list_skills_returns_builtin(srv):
    data = json.loads(_call(srv, "list_skills", {}))
    aliases = {s["alias"] for s in data}
    assert "@cr" in aliases


def test_get_skill_returns_markdown(srv):
    md = _call(srv, "get_skill", {"alias_or_name": "@cr"})
    assert md and md != "(not found)"
    assert len(md) > 50


def test_list_workflows_includes_plan(srv):
    data = json.loads(_call(srv, "list_workflows", {}))
    assert "plan" in data


def test_get_workflow_returns_markdown(srv):
    md = _call(srv, "get_workflow", {"name": "/plan"})
    assert md and md != "(not found)"


def test_list_agents_includes_council(srv):
    data = json.loads(_call(srv, "list_agents", {}))
    assert "adversary" in data
    assert "strategist" in data


def test_get_agent_returns_markdown(srv):
    md = _call(srv, "get_agent", {"name": "adversary"})
    assert md and md != "(not found)"


# --- error handling ------------------------------------------------------

def test_unknown_tool_returns_text(srv):
    text = _call(srv, "does_not_exist", {})
    assert "Unknown tool" in text


def test_handler_exception_is_caught(srv):
    # memory_invalidate without id key → KeyError inside handler → caught
    text = _call(srv, "memory_invalidate", {})
    assert "Error" in text
