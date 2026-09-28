"""check_rejected / check_approach, Competitive Response Plan Step 4."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from cairn.config.loader import load_config
from cairn.memory.memory import Memory
from cairn.mcp.server import CairnMCPServer


def _mk_memory(tmpdir: str, **overrides) -> Memory:
    config = load_config()
    config["memory"]["db_path"] = f"{tmpdir}/test.db"
    config["memory"]["global_db_path"] = overrides.pop("global_db_path", f"{tmpdir}/global.db")
    config["memory"]["async_store"] = False
    config["memory"].setdefault("dedup", {})["enabled"] = False
    return Memory(config, project_dir=Path(tmpdir))


def test_finds_rejected_alternative():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        mem.record_decision(
            "use httpOnly cookies for jwt storage",
            reasoning="mitigates XSS",
            alternatives=[{"option": "store the jwt token in localStorage", "rejected_because": "XSS token exfiltration"}],
        )
        rows = mem.check_rejected("cache the jwt token in localStorage")
        assert len(rows) == 1
        assert rows[0]["kind"] == "rejected_alternative"
        assert "XSS" in rows[0]["reason"]
        assert rows[0]["scope"] == "project"


def test_finds_abandoned_decision():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        a = mem.record_decision("use npm workspaces for the monorepo")
        mem.record_decision("use pnpm workspaces for the monorepo", supersedes=a,
                             chose_because="faster installs")
        rows = mem.check_rejected("switch back to npm workspaces for the monorepo")
        kinds = {r["kind"] for r in rows}
        assert "abandoned_decision" in kinds


def test_no_false_positive_on_unrelated_approach():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        mem.record_decision(
            "use httpOnly cookies for jwt storage",
            alternatives=[{"option": "store the jwt token in localStorage", "rejected_because": "XSS"}],
        )
        rows = mem.check_rejected("what font should the landing page use")
        assert rows == []


def test_requires_two_keyword_overlap_not_one():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        mem.record_decision(
            "use httpOnly cookies for jwt storage",
            alternatives=[{"option": "store the jwt token in localStorage", "rejected_because": "XSS"}],
        )
        # shares only one keyword ("storage") with the rejected option, must not match
        rows = mem.check_rejected("use cold storage for backups")
        assert rows == []


def test_finds_global_scope_rejection_from_different_project():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as shared:
        global_db = f"{shared}/global.db"
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as proj_a:
            mem_a = _mk_memory(proj_a, global_db_path=global_db)
            mem_a.record_decision(
                "always use pnpm for package management",
                alternatives=[{"option": "use npm for package management", "rejected_because": "slower installs"}],
                scope="global",
            )
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as proj_b:
            mem_b = _mk_memory(proj_b, global_db_path=global_db)
            rows = mem_b.check_rejected("let's use npm for package management here")
            assert len(rows) == 1
            assert rows[0]["scope"] == "global"
            assert str(rows[0]["decision_id"]).startswith("g")


def test_check_approach_mcp_tool():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        project = Path(td)
        cfg_dir = project / ".cairn"
        cfg_dir.mkdir()
        (cfg_dir / "config.yaml").write_text(
            f"memory:\n  db_path: {td}/mem.db\n  async_store: false\n"
            f"  global_db_path: {td}/global.db\n",
            encoding="utf-8",
        )
        srv = CairnMCPServer(project_dir=project)
        srv._handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "record_decision",
            "arguments": {"decision": "use httpOnly cookies for jwt storage",
                          "alternatives": [{"option": "store the jwt token in localStorage",
                                             "rejected_because": "XSS token exfiltration"}]},
        }})
        resp = srv._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "check_approach",
            "arguments": {"approach": "cache the jwt token in localStorage"},
        }})
        text = resp["result"]["content"][0]["text"]
        rows = json.loads(text)
        assert len(rows) == 1
        assert "XSS" in rows[0]["reason"]


def test_check_approach_tool_registered_in_schema():
    names = {t["name"] for t in CairnMCPServer()._tools}
    assert "check_approach" in names
