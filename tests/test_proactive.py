from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from cairn.mcp.server import CairnMCPServer
from cairn.memory.memory import Memory


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        r = Path(d)
        (r / ".cairn").mkdir()
        (r / ".cairn" / "config.yaml").write_text(
            "memory: {async_store: false, global_enabled: false}\n", encoding="utf-8"
        )
        yield r


def _mem(root: Path) -> Memory:
    return Memory({"memory": {"async_store": False, "global_enabled": False}}, project_dir=root)


def test_decisions_for_path_matches_ancestors_only(root):
    m = _mem(root)
    api = m.record_decision("API uses SQLAlchemy core, not the ORM", scope_path="apps/api")
    m.record_decision("Web uses server components", scope_path="apps/web")
    m.record_decision("Repo-wide: conventional commits")
    ids = [r["id"] for r in m.decisions_for_path("apps/api/routes/users.py")]
    assert ids == [api]
    abs_ids = [r["id"] for r in m.decisions_for_path(str(root / "apps" / "api" / "x.py"))]
    assert abs_ids == [api]
    assert m.decisions_for_path("docs/readme.md") == []


def test_mcp_initialize_has_instructions_and_new_tool(root):
    srv = CairnMCPServer(project_dir=root)
    init = srv._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert "decisions_for_path" in init["result"]["instructions"]
    srv.project.memory.record_decision("Use zod for validation", scope_path="src/api")
    out = srv._call_tool("decisions_for_path", {"path": "src/api/handler.ts"})
    assert "zod" in out
    boot = srv._call_tool("memory_boot", {})
    assert "Active decisions" in boot and "zod" in boot


def test_pretooluse_hook_emits_additional_context(root):
    _mem(root).record_decision("Handlers never touch the DB directly", scope_path="src/handlers")
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "cwd": str(root),
               "tool_input": {"file_path": str(root / "src" / "handlers" / "a.py")}}
    code = "import sys; sys.argv=['cairn','memory','for-path','--hook']; " \
           "from cairn.interface.cli import main; main()"
    out = subprocess.run([sys.executable, "-c", code], input=json.dumps(payload),
                         capture_output=True, text=True, cwd=root)
    data = json.loads(out.stdout)
    ctx = data["hookSpecificOutput"]["additionalContext"]
    assert data["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "never touch the DB" in ctx


def test_hook_is_silent_when_nothing_applies(root):
    payload = {"cwd": str(root), "tool_input": {"file_path": str(root / "README.md")}}
    code = "import sys; sys.argv=['cairn','memory','for-path','--hook']; " \
           "from cairn.interface.cli import main; main()"
    out = subprocess.run([sys.executable, "-c", code], input=json.dumps(payload),
                         capture_output=True, text=True, cwd=root)
    assert out.returncode == 0 and out.stdout.strip() == ""


def test_tool_calls_are_counted_locally(root):
    from cairn.memory.stats import load
    srv = CairnMCPServer(project_dir=root)
    srv._call_tool("record_decision", {"decision": "Use zod for validation", "scope_path": "src"})
    srv._call_tool("decisions_for_path", {"path": "src/a.ts"})
    srv._call_tool("decisions_for_path", {"path": "docs/x.md"})
    tools = load(root)["tools"]
    assert tools["record_decision"]["calls"] == 1
    assert tools["decisions_for_path"] == {"calls": 2, "hits": 1}
