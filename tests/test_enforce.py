"""Decision enforcement: `forbid` patterns block added lines, never existing code."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from cairn.memory import agents_md, enforce
from cairn.memory.memory import Memory, forbid_to_json

CLI = "import sys; sys.argv=['cairn', *{args!r}]; from cairn.interface.cli import main; main()"
TENACITY = r"^\s*(import|from) tenacity\b"


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        r = Path(d)
        (r / ".cairn").mkdir()
        (r / ".cairn" / "config.yaml").write_text(
            "memory: {async_store: false, global_enabled: false}\n", encoding="utf-8")
        yield r


def _mem(root: Path) -> Memory:
    return Memory({"memory": {"async_store": False, "global_enabled": False}}, project_dir=root)


def _cli(root: Path, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", CLI.format(args=list(args))], input=stdin,
                          capture_output=True, text=True, encoding="utf-8", cwd=root)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                   cwd=root, check=True, capture_output=True)


def _edit(root: Path, old: str, new: str, path: str = "jobs/tasks.py", tool: str = "Edit") -> str:
    return json.dumps({"session_id": "s1", "cwd": str(root), "hook_event_name": "PreToolUse",
                       "tool_name": tool, "tool_input": {"file_path": str(root / path),
                                                         "old_string": old, "new_string": new}})


# ---- pure functions -------------------------------------------------------------

def test_added_lines_counts_lines_as_a_multiset():
    assert enforce.added_lines("a\nb\n", "a\nb\nc\n") == ["c"]
    assert enforce.added_lines("x\n", "x\nx\n") == ["x"]
    assert enforce.added_lines("", "") == []


def test_edit_from_hook_reads_claude_and_gemini_edit_shapes(root):
    (root / "f.py").write_text("keep\n", encoding="utf-8")
    edit = {"tool_input": {"file_path": "f.py", "old_string": "a", "new_string": "a\nb"}}
    multi = {"tool_input": {"file_path": "f.py", "edits": [{"old_string": "", "new_string": "c"},
                                                          {"old_string": "d", "new_string": "e"}]}}
    write = {"tool_input": {"file_path": "f.py", "content": "keep\nnew\n"}}
    assert enforce.edit_from_hook(edit, root) == ("f.py", ["b"])
    assert enforce.edit_from_hook(multi, root) == ("f.py", ["c", "e"])
    assert enforce.edit_from_hook(write, root) == ("f.py", ["new"])
    assert enforce.edit_from_hook({"tool_input": {"command": "ls"}}, root) is None


@pytest.mark.parametrize("rel,expected", [
    (".cairn/decisions/x.md", True), ("AGENTS.md", True), ("sub/CLAUDE.md", True), ("GEMINI.md", True),
    ("src/agents.py", False), ("docs/notes.md", False),
])
def test_exempt_covers_cairns_own_files_and_agent_instruction_files(rel, expected):
    assert enforce.exempt(rel) is expected


def test_a_lone_string_is_one_pattern_not_one_per_character():
    assert forbid_to_json("import tenacity") == '["import tenacity"]'


def test_a_hand_edited_bad_pattern_is_dropped_with_a_warning_not_a_crash(capsys):
    assert forbid_to_json(["(", "ok"], strict=False) == '["ok"]'
    assert "not a valid regex" in capsys.readouterr().err


def test_files_outside_the_project_are_never_checked(root):
    m = _mem(root)
    m.record_decision("No tenacity", forbid=[TENACITY])
    outside = Path(tempfile.gettempdir()) / "elsewhere" / "x.py"
    assert enforce.violations(m, str(outside), ["import tenacity"]) is None
    assert enforce.violations(m, str(root / "x.py"), ["import tenacity"])


def test_forbid_patterns_are_validated_before_anything_is_written(root):
    assert forbid_to_json(None) is None and forbid_to_json(["", "  "]) is None
    with pytest.raises(ValueError, match="not a valid regex"):
        _mem(root).record_decision("Bad", forbid=["("])
    assert _mem(root).forbidden_for_path("x.py") == []


# ---- memory + violations ----------------------------------------------------------

def test_repo_wide_and_scoped_forbids_apply_where_they_should(root):
    m = _mem(root)
    m.record_decision("No tenacity", forbid=[TENACITY])
    m.record_decision("api: no print", forbid=[r"\bprint\("], scope_path="apps/api")
    assert [d["decision"] for d in m.forbidden_for_path("jobs/tasks.py")] == ["No tenacity"]
    assert {d["decision"] for d in m.forbidden_for_path("apps/api/x.py")} == {"No tenacity", "api: no print"}
    assert [d["decision"] for d in m.forbidden_for_path("apps/web/y.py")] == ["No tenacity"]


def test_only_added_lines_and_only_active_decisions_are_checked(root):
    m = _mem(root)
    old = m.record_decision("No tenacity", forbid=[TENACITY])
    found = enforce.violations(m, "jobs/tasks.py", ["x = 1", "from tenacity import retry"])
    assert [(v["id"], v["line"]) for v in found] == [(old, "from tenacity import retry")]
    assert enforce.violations(m, "jobs/tasks.py", ["x = 1"]) == []
    assert enforce.violations(m, "AGENTS.md", ["import tenacity"]) is None
    m.record_decision("tenacity is fine now", supersedes=old)
    assert enforce.violations(m, "jobs/tasks.py", ["import tenacity"]) is None


def test_forbid_survives_the_decision_file_round_trip(root):
    m = _mem(root)
    m.record_decision("No tenacity", forbid=[TENACITY])
    m.sync_decision_files(refresh_agents_md=False)
    files = list((root / ".cairn" / "decisions").glob("*.md"))
    assert len(files) == 1 and "forbid:" in files[0].read_text(encoding="utf-8")
    m.reindex_decisions()
    assert [d["forbid"] for d in _mem(root).forbidden_for_path("a.py")] == [[TENACITY]]


def test_agents_md_block_shows_the_forbidden_pattern(root):
    m = _mem(root)
    m.record_decision("No tenacity", forbid=[TENACITY])
    assert f"Forbidden, an edit adding it is blocked: `{TENACITY}`" in agents_md.body(m)


# ---- hooks and CLI, through the real CLI ----------------------------------------------

def test_guard_hook_blocks_a_forbidden_edit_and_passes_a_compliant_one(root):
    assert _cli(root, "decide", "Retry is hand-rolled", "--why", "tenacity hammered APIs",
                "--forbid", TENACITY).returncode == 0
    bad = _cli(root, "memory", "guard", "--hook", stdin=_edit(root, "", "from tenacity import retry\n"))
    assert bad.returncode == 2
    assert "Blocked by Cairn" in bad.stderr and "Retry is hand-rolled" in bad.stderr
    assert "tenacity hammered APIs" in bad.stderr
    ok = _cli(root, "memory", "guard", "--hook", stdin=_edit(root, "", "import time\n"))
    assert ok.returncode == 0 and ok.stderr == "" and ok.stdout == ""
    stats = json.loads((root / ".cairn" / "persistence" / "stats.json").read_text(encoding="utf-8"))
    assert stats["tools"]["guard"] == {"calls": 2, "hits": 1}


def test_the_default_per_file_hook_blocks_too(root):
    _cli(root, "decide", "Retry is hand-rolled", "--forbid", TENACITY)
    out = _cli(root, "memory", "for-path", "--hook", stdin=_edit(root, "", "import tenacity\n"))
    assert out.returncode == 2 and "Blocked by Cairn" in out.stderr


def test_guard_hook_reads_gemini_clis_before_tool_payload(root):
    _cli(root, "decide", "Retry is hand-rolled", "--forbid", TENACITY)
    payload = json.dumps({"cwd": str(root), "hook_event_name": "BeforeTool", "tool_name": "write_file",
                          "tool_input": {"file_path": "jobs/retry.py", "content": "import tenacity\n"}})
    out = _cli(root, "memory", "guard", "--hook", stdin=payload)
    assert out.returncode == 2 and "Blocked by Cairn" in out.stderr


def test_decide_rejects_an_invalid_pattern_without_recording(root):
    out = _cli(root, "decide", "Broken", "--forbid", "(")
    assert out.returncode == 1 and "not a valid regex" in out.stdout
    assert _mem(root).forbidden_for_path("a.py") == []


def test_guard_diff_mode_flags_only_lines_the_change_adds(root):
    _git(root, "init", "-q")
    (root / "legacy.py").write_text("from tenacity import retry\n", encoding="utf-8")
    (root / "tasks.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "legacy.py", "tasks.py")
    _git(root, "commit", "-q", "-m", "base")
    _cli(root, "decide", "Retry is hand-rolled", "--forbid", TENACITY)
    (root / "legacy.py").write_text("from tenacity import retry\n\n\ndef keep(): pass\n", encoding="utf-8")
    assert _cli(root, "memory", "guard").returncode == 0
    (root / "tasks.py").write_text("x = 1\nimport tenacity\n", encoding="utf-8")
    (root / "new.py").write_text("from tenacity import stop_after_attempt\n", encoding="utf-8")
    out = _cli(root, "memory", "guard")
    assert out.returncode == 1
    assert "tasks.py: import tenacity" in out.stdout and "new.py" in out.stdout
    assert "legacy.py" not in out.stdout
    _git(root, "add", "tasks.py")
    staged = _cli(root, "memory", "guard", "--staged")
    assert staged.returncode == 1 and "tasks.py" in staged.stdout and "new.py" not in staged.stdout


def test_mcp_record_decision_takes_forbid_and_reports_a_bad_pattern(root):
    from cairn.mcp.server import CairnMCPServer, _h_record_decision
    import os
    cwd = os.getcwd()
    os.chdir(root)
    try:
        srv = CairnMCPServer()
        res = json.loads(_h_record_decision(srv, {"decision": "No tenacity", "forbid": [TENACITY]}))
        assert res["kind"] == "decision"
        assert [d["forbid"] for d in srv.project.memory.forbidden_for_path("a.py")] == [[TENACITY]]
        assert "not a valid regex" in json.loads(_h_record_decision(srv, {"decision": "x", "forbid": ["("]}))["error"]
        _h_record_decision(srv, {"decision": "No print", "forbid": r"\bprint\("})
        assert [r"\bprint\("] in [d["forbid"] for d in srv.project.memory.forbidden_for_path("b.py")]
    finally:
        os.chdir(cwd)


# ---- enforcement at volume (simulated host payloads) --------------------------------

def _payloads(root: Path, line: str, i: int) -> dict:
    """One payload per way a host can add `line`: Claude Code Edit, Write and MultiEdit, Gemini CLI write_file and
    replace, across a spread of file names and positions in the new text."""
    path = f"pkg{i % 7}/mod{i}.py"
    body = f"x = {i}\n{line}\ny = {i}\n"
    shapes = [
        {"tool_name": "Edit", "tool_input": {"file_path": str(root / path), "old_string": "x = 0\n", "new_string": body}},
        {"tool_name": "Write", "tool_input": {"file_path": str(root / path), "content": body}},
        {"tool_name": "MultiEdit", "tool_input": {"file_path": str(root / path), "edits": [
            {"old_string": "a\n", "new_string": "b\n"}, {"old_string": "x = 0\n", "new_string": body}]}},
        {"tool_name": "write_file", "tool_input": {"file_path": path, "content": body}},
        {"tool_name": "replace", "tool_input": {"file_path": path, "old_string": "x = 0\n", "new_string": body}},
    ]
    return {"cwd": str(root), **shapes[i % len(shapes)]}


def test_a_hundred_forbidden_edits_are_all_blocked_and_a_hundred_clean_ones_all_pass(root):
    m = _mem(root)
    m.record_decision("Retry is hand-rolled", reasoning="tenacity hammered APIs", forbid=[TENACITY])
    forbidden = ["import tenacity", "from tenacity import retry", "  import tenacity", "from tenacity import (retry,",
                 "\timport tenacity as t"]
    clean = ["import time", "# tenacity is not used here", "retry_count = 3", "from tenacityx import y", "print('tenacity')"]
    blocked = passed = 0
    for i in range(100):
        got = enforce.edit_from_hook(_payloads(root, forbidden[i % 5], i), root)
        assert got is not None, i
        blocked += bool(enforce.violations(m, *got))
        got = enforce.edit_from_hook(_payloads(root, clean[i % 5], i), root)
        assert got is not None, i
        passed += not enforce.violations(m, *got)
    assert (blocked, passed) == (100, 100)
