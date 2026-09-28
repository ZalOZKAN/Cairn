"""AGENTS.md delivery, provenance and the cross-tool handoff."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from cairn.memory import agents_md, decision_files, handoff
from cairn.memory.memory import Memory

CLI = "import sys; sys.argv=['cairn', *{args!r}]; from cairn.interface.cli import main; main()"


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


def test_block_holds_every_active_decision_in_full(root):
    m = _mem(root)
    for i in range(60):
        m.record_decision(f"API rule number {i}", reasoning=f"because {i}", scope_path="api")
    m.record_decision("Money is integer cents", reasoning="float rounding",
                      alternatives=[{"option": "float", "rejected_because": "rounding"}], scope_path="shop")
    m.record_decision("Use conventional commits")
    text = agents_md.body(m)
    assert all(f"API rule number {i}**" in text for i in range(60))
    assert "### Repo-wide" in text and "### `api/`" in text and "### `shop/`" in text
    assert "- Why: float rounding" in text and "- Rejected: float (rounding)" in text
    assert "#1" not in text


def test_recording_refreshes_an_existing_block_but_never_creates_one(root):
    m = _mem(root)
    m.record_decision("Use pnpm", scope_path="web")
    assert not (root / "AGENTS.md").exists()
    assert agents_md.write(m, create=True)["written"]
    old = m.record_decision("Store JWT in localStorage", scope_path="auth")
    m.record_decision("Store JWT in an httpOnly cookie", supersedes=old, scope_path="auth")
    only = m.record_decision("Queue jobs on Celery", scope_path="jobs")
    m.invalidate(only, reason="moved to cron")
    text = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "httpOnly cookie" in text and "localStorage" not in text and "Celery" not in text
    assert "### `jobs/`" not in text


def test_only_the_marked_block_is_touched_and_crlf_is_kept(root):
    (root / "AGENTS.md").write_bytes(b"# My project\r\n\r\nHand-written notes.\r\n")
    m = _mem(root)
    m.record_decision("Use ruff")
    assert agents_md.write(m, create=True)["written"]
    assert not agents_md.write(m, create=True)["written"]
    raw = (root / "AGENTS.md").read_bytes().decode("utf-8")
    assert raw.startswith("# My project\r\n\r\nHand-written notes.\r\n")
    assert raw.count(agents_md.BEGIN) == 1 and "\n" not in raw.replace("\r\n", "")
    (root / "AGENTS.md").write_text(raw.replace("\r\n", "\n") + "\nMore notes after.\n", encoding="utf-8")
    m.record_decision("Use mypy")
    text = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "Use mypy" in text and text.endswith("More notes after.\n")


def test_block_is_identical_on_another_machine(root):
    m = _mem(root)
    for name in ("Use ruff", "Use pytest", "Money is integer cents", "Use zod"):
        m.record_decision(name, scope_path="shop" if "Money" in name else None)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        other = Path(d)
        (other / ".cairn").mkdir()
        shutil.copytree(root / ".cairn" / "decisions", other / ".cairn" / "decisions")
        m2 = _mem(other)
        m2.sync_decision_files()
        assert agents_md.body(m2) == agents_md.body(m)


def test_claude_md_gets_one_import_only_when_it_exists(root):
    assert agents_md.link_claude(root) is None
    (root / "CLAUDE.md").write_text(
        "# Rules\n\n<!-- cairn:begin -->\n@.cairn/rules/_index.md\n<!-- cairn:end -->\n", encoding="utf-8")
    assert agents_md.link_claude(root) == root / "CLAUDE.md"
    assert agents_md.link_claude(root) is None
    text = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.count("@AGENTS.md") == 1 and ".cairn/rules" not in text and text.startswith("# Rules")
    (root / "CLAUDE.md").unlink()
    (root / ".claude").mkdir()
    (root / ".claude" / "CLAUDE.md").write_text("x\n", encoding="utf-8")
    agents_md.link_claude(root)
    assert "@../AGENTS.md" in (root / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")


def test_link_gemini_does_nothing_for_a_project_that_never_used_gemini_cli(root):
    assert agents_md.link_gemini(root) is None
    assert not (root / ".gemini").exists()


def test_link_gemini_adds_agents_md_once_geminimd_shows_gemini_cli_is_used(root):
    (root / "GEMINI.md").write_text("# Notes\n", encoding="utf-8")
    changed = agents_md.link_gemini(root)
    assert changed == root / ".gemini" / "settings.json"
    data = json.loads(changed.read_text(encoding="utf-8"))
    assert data["context"]["fileName"] == ["GEMINI.md", "AGENTS.md"]
    assert agents_md.link_gemini(root) is None  # idempotent


def test_link_gemini_preserves_other_settings_and_filenames(root):
    settings = root / ".gemini" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({
        "context": {"fileName": ["GEMINI.md", "CONTEXT.md"]},
        "general": {"vimMode": True},
    }), encoding="utf-8")
    agents_md.link_gemini(root)
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["context"]["fileName"] == ["GEMINI.md", "CONTEXT.md", "AGENTS.md"]
    assert data["general"] == {"vimMode": True}


def test_link_gemini_does_not_clobber_unparseable_settings(root):
    settings = root / ".gemini" / "settings.json"
    settings.parent.mkdir()
    settings.write_text("{not json", encoding="utf-8")
    assert agents_md.link_gemini(root) is None
    assert settings.read_text(encoding="utf-8") == "{not json"


def test_sync_and_check_cli(root):
    _mem(root).record_decision("Use pnpm", scope_path="web")
    assert _cli(root, "check").returncode == 1
    assert _cli(root, "sync").returncode == 0
    assert "Use pnpm" in (root / "AGENTS.md").read_text(encoding="utf-8")
    assert _cli(root, "check").returncode == 0
    text = (root / "AGENTS.md").read_text(encoding="utf-8")
    (root / "AGENTS.md").write_text(text.replace("Use pnpm", "Use npm"), encoding="utf-8")
    bad = _cli(root, "check")
    assert bad.returncode == 1 and "out of date" in bad.stdout


def test_disabled_in_config_means_agents_md_is_left_alone(root):
    (root / ".cairn" / "config.yaml").write_text(
        "memory: {async_store: false, global_enabled: false, agents_md: {enabled: false}}\n", encoding="utf-8")
    assert _cli(root, "sync").returncode == 0
    assert not (root / "AGENTS.md").exists()
    assert _cli(root, "check").returncode == 0


def test_decide_cli_records_with_alternatives_and_provenance(root):
    out = _cli(root, "decide", "Money is integer cents", "--why", "float rounding broke invoices",
               "--rejected", "float", "--because", "rounding", "--rejected", "Decimal", "--path", "shop")
    assert out.returncode == 0, out.stdout + out.stderr
    f = next((root / ".cairn" / "decisions").glob("*.md"))
    d = decision_files.parse(f.read_text(encoding="utf-8"))
    assert d["recorded_by"] == "cli" and d["path"] == "shop"
    assert d["alternatives"] == [{"option": "float", "rejected_because": "rounding"},
                                 {"option": "Decimal", "rejected_because": ""}]


def test_mcp_records_the_client_name_and_it_survives_a_rebuild(root):
    from cairn.mcp.server import CairnMCPServer
    srv = CairnMCPServer(project_dir=root)
    srv._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"clientInfo": {"name": "cursor", "version": "1.0"}}})
    srv._call_tool("record_decision", {"decision": "Use zod for validation", "scope_path": "web"})
    f = next((root / ".cairn" / "decisions").glob("*.md"))
    assert "recorded_by: cursor" in f.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        other = Path(d)
        (other / ".cairn").mkdir()
        shutil.copytree(root / ".cairn" / "decisions", other / ".cairn" / "decisions")
        m = _mem(other)
        m.sync_decision_files()
        assert "(via cursor)" in handoff.recent_decisions(m)


def test_handoff_note_plus_git_state_and_decisions(root):
    _git(root, "init", "-q")
    (root / "shop").mkdir()
    (root / "shop" / "models.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "Add shop models")
    (root / "shop" / "pricing.py").write_text("y = 2\n", encoding="utf-8")
    _mem(root).record_decision("Money is integer cents", scope_path="shop", recorded_by="antigravity")

    out = _cli(root, "handoff", "Added the price field", "--next", "Write apply_discount in shop/pricing.py")
    assert out.returncode == 0, out.stdout + out.stderr
    text = (root / ".cairn" / "persistence" / "session-handoff.md").read_text(encoding="utf-8")
    assert "## Continuation Point\nWrite apply_discount" in text
    assert "Added the price field" in text and "_Left by cli at" in text
    assert "[shop] Money is integer cents (via antigravity)" in text
    assert "shop/pricing.py" in text and "Add shop models" in text and ".cairn/" not in text.split(
        "## Working Tree")[1]

    (root / "shop" / "pricing.py").unlink()
    handoff.refresh(_mem(root), root / ".cairn" / "persistence" / "session-handoff.md")
    again = (root / ".cairn" / "persistence" / "session-handoff.md").read_text(encoding="utf-8")
    tree = again.split("## Working Tree")[1]
    assert "Write apply_discount" in again and "shop/pricing.py" not in tree and "No uncommitted" in tree
    assert again.count("## Working Tree") == 1 and again.count("## Recent Decisions") == 1


def test_mcp_note_is_kept_when_the_host_closes_the_server(root):
    from cairn.mcp.server import CairnMCPServer
    srv = CairnMCPServer(project_dir=root)
    srv._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"clientInfo": {"name": "claude-code"}}})
    srv._call_tool("memory_store", {"prompt": "p", "response": "r"})
    res = json.loads(srv._call_tool("memory_save", {"summary": "Half of the pricing work",
                                                    "next_step": "Finish apply_discount"}))
    assert res["ok"] is True
    srv._shutdown()
    text = (root / ".cairn" / "persistence" / "session-handoff.md").read_text(encoding="utf-8")
    assert "Finish apply_discount" in text and "_Left by claude-code" in text
    assert "Auto-saved" not in text and "## Working Tree" in text


def test_shutdown_without_a_note_still_leaves_git_state(root):
    from cairn.mcp.server import CairnMCPServer
    srv = CairnMCPServer(project_dir=root)
    srv._call_tool("record_decision", {"decision": "Use zod"})
    srv._shutdown()
    text = (root / ".cairn" / "persistence" / "session-handoff.md").read_text(encoding="utf-8")
    assert "## Working Tree" in text and "Use zod" in text


def test_shutdown_imports_a_hand_edit_the_agent_never_synced(root):
    """The benchmark's own real-world finding: an agent edits the AGENTS.md
    block by hand and then stops, without calling any Cairn tool that would
    have synced it. Without this, the edit sits unimported until something
    else triggers a sync; with it, closing the server is enough."""
    from cairn.mcp.server import CairnMCPServer
    srv = CairnMCPServer(project_dir=root)
    srv._call_tool("record_decision", {"decision": "Outbound calls use requests", "scope_path": "clients"})
    assert agents_md.write(srv.project.memory, create=True)["written"]
    text = (root / "AGENTS.md").read_text(encoding="utf-8")
    edited = text.replace(
        "- **Outbound calls use requests**", "- **Outbound calls use httpx**\n  - Why: async support")
    assert edited != text
    (root / "AGENTS.md").write_text(edited, encoding="utf-8")

    srv._shutdown()

    m = _mem(root)
    remaining = m.decisions_for_path("clients/x.py")
    assert len(remaining) == 1 and "httpx" in remaining[0]["decision"]
    assert m.get_full(remaining[0]["id"])["reasoning"] == "async support"


def test_new_session_imports_a_hand_edit_at_initialize_even_if_it_never_calls_a_tool(root):
    """The gap `_shutdown()` alone cannot close: the previous session's host
    may terminate its MCP server without a clean stdin EOF at all (observed
    on the benchmark), and even when it does, `_shutdown()` only runs the
    import if `.project` was ever touched, which never happens in a session
    whose task does not need any Cairn tool call. The next session's `initialize`
    is the one message every host sends regardless, so that is where this
    must be caught instead."""
    from cairn.mcp.server import CairnMCPServer
    srv1 = CairnMCPServer(project_dir=root)
    srv1._call_tool("record_decision", {"decision": "Outbound calls use requests", "scope_path": "clients"})
    assert agents_md.write(srv1.project.memory, create=True)["written"]
    text = (root / "AGENTS.md").read_text(encoding="utf-8")
    edited = text.replace(
        "- **Outbound calls use requests**", "- **Outbound calls use httpx**\n  - Why: async support")
    assert edited != text
    (root / "AGENTS.md").write_text(edited, encoding="utf-8")
    # srv1 is simply discarded here, no _shutdown() call: the host killed it
    # outright, or it never gets the chance to unwind at all.

    srv2 = CairnMCPServer(project_dir=root)
    srv2._handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"clientInfo": {"name": "claude-code"}}})
    # no tool call at all on srv2 beyond this

    m = _mem(root)
    remaining = m.decisions_for_path("clients/x.py")
    assert len(remaining) == 1 and "httpx" in remaining[0]["decision"]


def test_init_writes_gitignore_agents_md_and_no_helper(root):
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        fresh = Path(d)
        (fresh / "AGENTS.md").write_text("# Existing\n", encoding="utf-8")
        out = _cli(fresh, "init")
        assert out.returncode == 0, out.stdout + out.stderr
        from cairn.interface.commands.init import GITIGNORE_TEMPLATE
        assert (fresh / ".cairn" / ".gitignore").read_text(encoding="utf-8") == GITIGNORE_TEMPLATE
        text = (fresh / "AGENTS.md").read_text(encoding="utf-8")
        assert text.startswith("# Existing\n") and "No decisions recorded yet." in text
        assert "memory_helper_model: null" in (fresh / ".cairn" / "config.yaml").read_text(encoding="utf-8")


def test_decisions_for_path_is_never_truncated(root):
    m = _mem(root)
    for i in range(12):
        m.record_decision(f"billing rule {i}", scope_path="billing")
    assert len(m.decisions_for_path("billing/invoice.py")) == 12


def test_hook_reminds_each_decision_once_per_session(root):
    _mem(root).record_decision("Handlers never touch the DB directly", scope_path="src")

    def fire(session, path):
        payload = {"session_id": session, "cwd": str(root), "tool_input": {"file_path": str(root / path)}}
        return _cli(root, "memory", "for-path", "--hook", stdin=json.dumps(payload)).stdout.strip()

    assert "never touch the DB" in fire("s1", "src/a.py")
    assert fire("s1", "src/a.py") == ""
    assert fire("s1", "src/b.py") == ""
    assert "never touch the DB" in fire("s2", "src/a.py")


def test_doctor_reports_what_is_on_and_off(root):
    (root / "CLAUDE.md").write_text("# mine\n", encoding="utf-8")
    out = _cli(root, "doctor").stdout
    assert "no Cairn block" in out and "skips AGENTS.md" in out and "not configured" in out
    _cli(root, "sync")
    out = _cli(root, "doctor").stdout
    assert "block up to date" in out and "imports AGENTS.md" in out


def test_working_tree_in_a_repo_with_no_commits_yet(root):
    _git(root, "init", "-q")
    (root / "a.py").write_text("x\n", encoding="utf-8")
    text = handoff.working_tree(root)
    assert "Not a git repository" not in text and "a.py" in text


def _mcp_stdio(root: Path, req: str, io_encoding: str | None = None) -> subprocess.CompletedProcess:
    """Run `cairn mcp serve` over stdio. `io_encoding` pins the child's own
    console/locale encoding (e.g. "cp1252"), overriding whatever the test
    runner's own shell happens to set, so these tests do not depend on it."""
    env = {**os.environ}
    if io_encoding:
        env["PYTHONIOENCODING"] = io_encoding
    else:
        env.pop("PYTHONIOENCODING", None)
    return subprocess.run([sys.executable, "-m", "cairn.interface.cli", "mcp", "serve"],
                          input=req.encode("utf-8"), capture_output=True, cwd=root, env=env)


def test_mcp_server_reads_client_name_even_after_a_bom_under_cp1252_stdio(root):
    req = ('\ufeff{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"clientInfo":{"name":"cursor"}}}\n'
           '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"record_decision",'
           '"arguments":{"decision":"Use zod"}}}\n')
    out = _mcp_stdio(root, req, io_encoding="cp1252")
    assert b'"id": 1' in out.stdout, out.stdout + out.stderr
    f = next((root / ".cairn" / "decisions").glob("*.md"))
    assert "recorded_by: cursor" in f.read_text(encoding="utf-8")


def test_mcp_server_does_not_crash_on_non_ascii_decision_text_under_cp1252_stdio(root):
    req = ('{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"record_decision",'
           '"arguments":{"decision":"Utilise \\u00e9v\\u00e9nements horodat\\u00e9s en UTC \\u2014 \\u4e2d\\u6587"}}}\n')
    out = _mcp_stdio(root, req, io_encoding="cp1252")
    assert out.returncode == 0 and b"Error" not in out.stdout, out.stdout + out.stderr
    f = next((root / ".cairn" / "decisions").glob("*.md"))
    assert "horodat\u00e9s" in f.read_text(encoding="utf-8")


# ---- two-way sync: hand edits to the AGENTS.md block --------------------

def test_a_brand_new_decision_does_not_self_invalidate(root):
    """The file is always one regeneration behind the decision that just
    triggered it (that regeneration is what this call is doing); a new
    decision in a scope the file has never shown before must not be misread
    as something a human deleted from a scope that was never there."""
    m = _mem(root)
    m.record_decision("Use pnpm", scope_path="web")
    assert agents_md.write(m, create=True)["written"]
    new_id = m.record_decision("Use argon2id for password hashing", scope_path="auth")
    text = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "argon2id" in text
    assert m.get_full(new_id)["valid_until"] is None


def test_hand_added_bullet_is_recorded_as_a_new_decision(root):
    m = _mem(root)
    m.record_decision("Use pnpm", scope_path="web")
    assert agents_md.write(m, create=True)["written"]
    text = (root / "AGENTS.md").read_text(encoding="utf-8")
    edited = text.replace(
        "- **Use pnpm**",
        "- **Use pnpm**\n\n- **Use Vitest for unit tests**\n"
        "  - Why: Fast and shares Vite config.\n  - Rejected: Jest (slow)",
    )
    assert edited != text
    (root / "AGENTS.md").write_text(edited, encoding="utf-8")

    result = agents_md.write(m)
    assert result == {"written": True, "invalidated": 0, "recorded": 1, "superseded": 0}
    added = next(r for r in m.decisions_for_path("web/x.ts") if "Vitest" in r["decision"])
    full = m.get_full(added["id"])
    assert full["recorded_by"] == "agents.md" and full["reasoning"] == "Fast and shares Vite config."
    assert full["alternatives"] == [{"option": "Jest", "rejected_because": "slow"}]


def test_hand_removed_bullet_invalidates_that_decision_only(root):
    m = _mem(root)
    a = m.record_decision("Use argon2id for password hashing", scope_path="auth")
    m.record_decision("Rotate refresh tokens every 30 days", scope_path="auth")
    assert agents_md.write(m, create=True)["written"]
    text = (root / "AGENTS.md").read_text(encoding="utf-8")
    edited = text.replace("- **Use argon2id for password hashing**\n", "")
    assert edited != text
    (root / "AGENTS.md").write_text(edited, encoding="utf-8")

    result = agents_md.write(m)
    # the file already matches what regenerating from the now-updated DB
    # produces (the hand edit left exactly the surviving bullet), so there is
    # nothing left to write back
    assert result == {"written": False, "invalidated": 1, "recorded": 0, "superseded": 0}
    full = m.get_full(a)
    assert full["valid_until"] is not None
    assert full["invalidated_reason"] == "removed from AGENTS.md by hand edit"
    remaining = m.decisions_for_path("auth/x.py")
    assert len(remaining) == 1 and "Rotate refresh" in remaining[0]["decision"]


def test_hand_edited_bullet_text_is_recorded_as_a_supersede(root):
    m = _mem(root)
    old = m.record_decision("Outbound HTTP calls use the requests library", scope_path="clients")
    assert agents_md.write(m, create=True)["written"]
    text = (root / "AGENTS.md").read_text(encoding="utf-8")
    edited = text.replace(
        "- **Outbound HTTP calls use the requests library**",
        "- **Outbound HTTP calls use httpx**\n  - Why: Services moved to async.",
    )
    assert edited != text
    (root / "AGENTS.md").write_text(edited, encoding="utf-8")

    result = agents_md.write(m)
    # again, the hand edit already left the file in canonical form
    assert result == {"written": False, "invalidated": 0, "recorded": 0, "superseded": 1}
    old_full = m.get_full(old)
    new_row = next(r for r in m.decisions_for_path("clients/x.py") if "httpx" in r["decision"])
    new_full = m.get_full(new_row["id"])
    assert old_full["valid_until"] is not None
    assert new_full["recorded_by"] == "agents.md" and new_full["reasoning"] == "Services moved to async."
    assert new_full["supersedes"] == old and old_full["superseded_by"] == new_row["id"]


def test_sync_cli_reports_edits_imported_from_agents_md(root):
    m = _mem(root)
    m.record_decision("Outbound HTTP calls use the requests library", scope_path="clients")
    assert agents_md.write(m, create=True)["written"]
    text = (root / "AGENTS.md").read_text(encoding="utf-8")
    edited = text.replace(
        "- **Outbound HTTP calls use the requests library**",
        "- **Outbound HTTP calls use httpx**",
    )
    (root / "AGENTS.md").write_text(edited, encoding="utf-8")
    out = _cli(root, "sync")
    assert out.returncode == 0 and "superseded from AGENTS.md edits" in out.stdout


# ---- possible_supersedes / replaces --------------------------------------

def test_record_decision_suggests_possible_supersedes_in_the_same_scope_only(root):
    from cairn.mcp.server import CairnMCPServer
    srv = CairnMCPServer(project_dir=root)
    srv._call_tool("record_decision",
                   {"decision": "Outbound HTTP calls use the requests library", "scope_path": "clients"})

    out = json.loads(srv._call_tool(
        "record_decision", {"decision": "Outbound HTTP calls use httpx", "scope_path": "clients"}))
    assert len(out["possible_supersedes"]) == 1
    old_id = out["possible_supersedes"][0]["id"]
    assert "requests library" in out["possible_supersedes"][0]["decision"]

    # explicit supersedes: no advisory needed
    out2 = json.loads(srv._call_tool("record_decision", {
        "decision": "Outbound HTTP calls use httpx everywhere", "scope_path": "clients", "supersedes": old_id}))
    assert "possible_supersedes" not in out2

    # a different scope, even with the same words, is not suggested
    out3 = json.loads(srv._call_tool(
        "record_decision", {"decision": "Outbound HTTP calls use httpx", "scope_path": "svc"}))
    assert "possible_supersedes" not in out3

    # global decisions are never advised (they are not scoped to a path);
    # global_db_path is pinned inside `root` so this never touches ~/.cairn/
    (root / ".cairn" / "config.yaml").write_text(
        f"memory: {{async_store: false, global_enabled: true, global_db_path: {root.as_posix()}/global.db}}\n",
        encoding="utf-8")
    srv2 = CairnMCPServer(project_dir=root)
    out4 = json.loads(srv2._call_tool("record_decision", {
        "decision": "Outbound HTTP calls use httpx", "scope_path": "clients", "scope": "global"}))
    assert "possible_supersedes" not in out4


def test_possible_supersedes_ignores_generic_words_shared_across_unrelated_decisions(root):
    from cairn.mcp.server import CairnMCPServer
    srv = CairnMCPServer(project_dir=root)
    # both mention "log entries" and "use", but are about different topics;
    # 3 raw shared stems would have matched the old ">=2" rule
    srv._call_tool("record_decision", {
        "decision": "Admin actions use audit log entries for compliance", "scope_path": "api"})
    out = json.loads(srv._call_tool("record_decision", {
        "decision": "Search results use relevance log entries for ranking", "scope_path": "api"}))
    assert "possible_supersedes" not in out


def test_record_decision_replaces_resolves_an_unambiguous_match(root):
    from cairn.mcp.server import CairnMCPServer
    srv = CairnMCPServer(project_dir=root)
    srv._call_tool("record_decision",
                   {"decision": "Outbound HTTP calls use the requests library", "scope_path": "clients"})
    out = json.loads(srv._call_tool("record_decision", {
        "decision": "Outbound HTTP calls use httpx", "scope_path": "clients", "replaces": "the requests library"}))
    assert "warning" not in out and "replace_candidates" not in out
    remaining = srv.project.memory.decisions_for_path("clients/x.py")
    assert len(remaining) == 1 and "httpx" in remaining[0]["decision"]


def test_record_decision_replaces_ambiguous_match_invalidates_nothing(root):
    from cairn.mcp.server import CairnMCPServer
    srv = CairnMCPServer(project_dir=root)
    srv._call_tool("record_decision",
                   {"decision": "Outbound HTTP calls use the requests library", "scope_path": "clients"})
    srv._call_tool("record_decision",
                   {"decision": "Internal HTTP mocks use the requests library format", "scope_path": "testing"})
    out = json.loads(srv._call_tool("record_decision", {
        "decision": "Outbound HTTP calls use httpx", "scope_path": "clients", "replaces": "the requests library"}))
    assert len(out["replace_candidates"]) == 2
    remaining = srv.project.memory.decisions_for_path("clients/x.py")
    assert any("requests" in r["decision"] for r in remaining)


def test_decide_cli_replaces_resolves_an_unambiguous_match(root):
    m = _mem(root)
    m.record_decision("Outbound HTTP calls use the requests library", scope_path="clients")
    out = _cli(root, "decide", "Outbound HTTP calls use httpx",
              "--path", "clients", "--replaces", "the requests library")
    assert out.returncode == 0, out.stdout + out.stderr
    remaining = m.decisions_for_path("clients/x.py")
    assert len(remaining) == 1 and "httpx" in remaining[0]["decision"]


def test_decide_cli_replaces_ambiguous_lists_candidates_and_still_records(root):
    m = _mem(root)
    m.record_decision("Outbound HTTP calls use the requests library", scope_path="clients")
    m.record_decision("Internal HTTP mocks use the requests library format", scope_path="testing")
    out = _cli(root, "decide", "Outbound HTTP calls use httpx",
              "--path", "clients", "--replaces", "the requests library")
    assert out.returncode == 0 and "matched more than one decision" in out.stdout
    remaining = m.decisions_for_path("clients/x.py")
    assert any("requests" in r["decision"] for r in remaining) and any("httpx" in r["decision"] for r in remaining)


def test_decide_cli_prints_possible_supersedes_hint_and_invalidates_nothing(root):
    m = _mem(root)
    m.record_decision("Outbound HTTP calls use the requests library", scope_path="clients")
    out = _cli(root, "decide", "Outbound HTTP calls use httpx", "--path", "clients")
    assert out.returncode == 0
    assert "may replace an existing decision" in out.stdout and "requests library" in out.stdout
    remaining = m.decisions_for_path("clients/x.py")
    assert len(remaining) == 2
