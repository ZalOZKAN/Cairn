"""MCP server tool-handler tests: no stdio, just direct dispatch."""
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
            f"memory:\n  db_path: {td}/mem.db\n  async_store: false\n"
            f"  global_db_path: {td}/global.db\n",
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
        "record_decision", "memory_get", "memory_thread",
        "decision_audit_score", "check_approach", "memory_save",
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
    assert len(resp["result"]["tools"]) == 14


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


def test_memory_recall_and_search_path_scoping(srv):
    _call(srv, "memory_store",
          {"prompt": "path scoping api topic", "response": "api-only detail",
           "scope_path": "apps/api"})
    _call(srv, "memory_store",
          {"prompt": "path scoping web topic", "response": "web-only detail",
           "scope_path": "apps/web"})

    api_recall = _call(srv, "memory_recall", {"query": "path scoping", "path": "apps/api"})
    assert "api-only detail" in api_recall
    assert "web-only detail" not in api_recall

    api_search = json.loads(_call(srv, "memory_search", {"query": "path scoping", "path": "apps/api"}))
    assert any("api-only detail" in r["response"] for r in api_search)
    assert not any("web-only detail" in r["response"] for r in api_search)


def test_memory_recent_and_search(srv):
    _call(srv, "memory_store", {"prompt": "alpha topic one", "response": "answer one"})
    _call(srv, "memory_store", {"prompt": "alpha topic two", "response": "answer two"})
    recent = json.loads(_call(srv, "memory_recent", {"n": 5}))
    assert len(recent) == 2
    found = json.loads(_call(srv, "memory_search", {"query": "alpha"}))
    assert len(found) == 2


def test_memory_invalidate_hides_entry(srv):
    _call(srv, "memory_store", {"prompt": "temporary fact", "response": "old answer"})
    # need to look up id via direct sqlite, recent doesn't return ids
    import sqlite3
    with sqlite3.connect(srv.project.memory.db_path) as conn:
        row_id = conn.execute("SELECT id FROM memories LIMIT 1").fetchone()[0]
    assert _call(srv, "memory_invalidate", {"id": row_id, "reason": "outdated"}) == "invalidated"
    found = json.loads(_call(srv, "memory_search", {"query": "temporary"}))
    assert found == []


# --- decision provenance (Phase 2 Stream B) ------------------------------

def test_record_decision_minimum_then_get(srv):
    raw = _call(srv, "record_decision", {"decision": "Use SQLite for local dev"})
    new_id = json.loads(raw)["id"]
    full = json.loads(_call(srv, "memory_get", {"id": new_id}))
    assert full["decision"] == "Use SQLite for local dev"
    assert full["kind"] == "decision"
    assert full["alternatives"] == []
    assert full["evidence"] == []


def test_record_decision_with_alternatives_and_evidence(srv):
    raw = _call(srv, "record_decision", {
        "decision": "Use httpOnly cookies for JWT",
        "reasoning": "Mitigates XSS token exfil",
        "alternatives": [{"option": "localStorage", "rejected_because": "XSS"}],
        "evidence": [{"source": "OWASP cheatsheet", "claim": "httpOnly blocks JS read"}],
        "chose_because": "security",
    })
    new_id = json.loads(raw)["id"]
    score = json.loads(_call(srv, "decision_audit_score", {"id": new_id}))
    # has reasoning + alternatives + evidence; no supersedes yet
    assert score["score"] == 3
    assert "has_supersedes" in score["missing"]


def test_memory_thread_walks_supersedes_chain(srv):
    a = json.loads(_call(srv, "record_decision", {"decision": "Use localStorage for JWT"}))["id"]
    b = json.loads(_call(srv, "record_decision",
                         {"decision": "Use httpOnly cookies for JWT", "supersedes": a}))["id"]
    c = json.loads(_call(srv, "record_decision",
                         {"decision": "Add CSRF token on top of httpOnly cookies",
                          "supersedes": b}))["id"]
    # walking by id (passed as string into the MCP tool)
    chain = json.loads(_call(srv, "memory_thread", {"topic_or_id": str(a)}))
    assert [r["id"] for r in chain] == [a, b, c]
    assert chain[-1]["active"] is True
    assert chain[0]["active"] is False


def test_memory_thread_by_keyword(srv):
    _call(srv, "record_decision", {"decision": "Use SQLite for local development storage"})
    _call(srv, "record_decision", {"decision": "Use Redis for cache layer"})
    matches = json.loads(_call(srv, "memory_thread", {"topic_or_id": "SQLite storage"}))
    assert any("SQLite" in r["decision"] for r in matches)
    assert all("Redis" not in r["decision"] for r in matches)


def test_memory_thread_by_keyword_is_chronological_across_scopes(srv):
    _call(srv, "record_decision", {"decision": "Use SQLite for storage"})
    _call(srv, "record_decision", {"decision": "Use Postgres for storage"})
    matches = json.loads(_call(srv, "memory_thread", {"topic_or_id": "storage"}))
    assert [r["decision"] for r in matches] == [
        "Use SQLite for storage",
        "Use Postgres for storage",
    ]


def test_superseded_decision_does_not_get_supersedes_audit_point(srv):
    old = json.loads(_call(srv, "record_decision", {
        "decision": "Use localStorage for JWT",
        "reasoning": "initial prototype",
    }))["id"]
    _call(srv, "record_decision", {
        "decision": "Use httpOnly cookies for JWT",
        "reasoning": "reduces token exposure",
        "supersedes": old,
    })
    score = json.loads(_call(srv, "decision_audit_score", {"id": old}))
    assert score["checks"]["has_supersedes"] is False
    assert score["score"] == 1


def test_record_decision_returns_proposal_only_duplicate_candidate(srv):
    _call(srv, "record_decision", {
        "decision": "Use SQLite for local storage",
        "reasoning": "Keeps the project local",
    })
    result = json.loads(_call(srv, "record_decision", {
        "decision": "Use SQLite for local storage",
        "reasoning": "Keeps the project local",
    }))
    assert result["duplicate_candidates"][0]["relation"] == "exact-duplicate"


def test_memory_thread_supports_as_of_validity_windows(srv):
    old = json.loads(_call(srv, "record_decision", {
        "decision": "Use SQLite for the cache",
        "valid_from": "2025-01-01T00:00:00+00:00",
    }))["id"]
    _call(srv, "record_decision", {
        "decision": "Use Redis for the cache",
        "supersedes": old,
        "valid_from": "2026-01-01T00:00:00+00:00",
    })
    before = json.loads(_call(srv, "memory_thread", {
        "topic_or_id": str(old), "as_of": "2025-06-01T00:00:00+00:00",
    }))
    after = json.loads(_call(srv, "memory_thread", {
        "topic_or_id": str(old), "as_of": "2026-06-01T00:00:00+00:00",
    }))
    assert [row["decision"] for row in before] == ["Use SQLite for the cache"]
    assert [row["decision"] for row in after] == ["Use Redis for the cache"]


def test_record_decision_rejects_future_valid_from(srv):
    out = json.loads(_call(srv, "record_decision", {
        "decision": "Use SQLite for the cache",
        "valid_from": "2999-01-01T00:00:00+00:00",
    }))
    assert "error" in out and "future" in out["error"]


def test_memory_thread_orders_mixed_timezones_by_instant(srv):
    first = json.loads(_call(srv, "record_decision", {"decision": "Use SQLite for sessions"}))["id"]
    second = json.loads(_call(srv, "record_decision", {"decision": "Use Redis for sessions"}))["id"]
    rows = json.loads(_call(srv, "memory_thread", {"topic": "sessions"}))
    assert [r["id"] for r in rows] == [first, second]


def test_memory_boot_token_budget_bounds_whole_output(srv):
    for i in range(12):
        _call(srv, "record_decision", {
            "decision": f"Decision number {i} about module{i}",
            "reasoning": "long reasoning " * 40,
        })
    full = _call(srv, "memory_boot", {"decision_mode": "full"})
    capped = _call(srv, "memory_boot", {"decision_mode": "full", "token_budget": 400})
    index = _call(srv, "memory_boot", {})
    assert len(capped) < len(full)
    assert len(index) < len(full)
    assert "[#1]" in capped or "[#1]" in index


# --- auto-save triggers (Phase 2 Stream A) -------------------------------

def _stub_helper(srv, payload: dict | None):
    """Patch project.helper_call to return a fixed JSON string (or None)."""
    if payload is None:
        srv.project.helper_call = lambda prompt, system=None: None  # type: ignore
    else:
        srv.project.config["memory_helper_model"] = "stub/helper"
        srv.project.helper_call = lambda prompt, system=None: json.dumps(payload)  # type: ignore


def test_memory_save_tool_returns_no_extraction_when_helper_unavailable(srv):
    _stub_helper(srv, None)
    _call(srv, "memory_store", {"prompt": "p", "response": "r"})
    out = _call(srv, "memory_save", {})
    data = json.loads(out)
    assert data["ok"] is True
    assert data.get("fallback") is True


def test_memory_save_tool_reports_counts_when_helper_works(srv):
    _stub_helper(srv, {
        "facts": ["sqlite is used"],
        "decisions": [{"decision": "use sqlite", "reason": "no infra"}],
        "tasks_completed": [],
        "tasks_in_progress": [],
        "errors_solved": [],
        "next_session_start": "",
    })
    _call(srv, "memory_store", {"prompt": "decided on sqlite", "response": "no infra"})
    out = json.loads(_call(srv, "memory_save", {}))
    assert out["ok"] is True
    assert out["facts"] == 1
    assert out["decisions"] == 1


def test_n_store_threshold_fires_autosave(srv, monkeypatch):
    """Trigger #2: after every_n_stores, autosave should be spawned (we mock the spawn)."""
    fired: list[str] = []
    monkeypatch.setattr(srv, "_spawn_autosave", lambda reason: fired.append(reason))
    srv.project.config.setdefault("memory", {}).setdefault("auto_save", {})
    srv.project.config["memory"]["auto_save"].update({
        "enabled": True, "every_n_stores": 3,
        "cooldown_minutes": 0, "idle_minutes": 0,
    })
    for i in range(3):
        _call(srv, "memory_store", {"prompt": f"p{i}", "response": f"r{i}"})
    assert "threshold" in fired


def test_idle_timer_resets_on_each_store(srv, monkeypatch):
    """Trigger #4: idle Timer is set/reset by store events."""
    srv.project.config.setdefault("memory", {}).setdefault("auto_save", {})
    srv.project.config["memory"]["auto_save"].update({
        "enabled": True, "every_n_stores": 999,
        "cooldown_minutes": 0, "idle_minutes": 60,
    })
    assert srv._idle_timer is None
    _call(srv, "memory_store", {"prompt": "p1", "response": "r1"})
    first = srv._idle_timer
    assert first is not None
    _call(srv, "memory_store", {"prompt": "p2", "response": "r2"})
    assert srv._idle_timer is not first  # got replaced
    # cleanup
    if srv._idle_timer:
        srv._idle_timer.cancel()


def test_shutdown_cancels_timer_and_flushes(srv, monkeypatch):
    """Trigger #5: _shutdown cancels idle timer; safe even with no project."""
    srv.project.config.setdefault("memory", {}).setdefault("auto_save", {})
    srv.project.config["memory"]["auto_save"].update({
        "enabled": True, "every_n_stores": 999,
        "cooldown_minutes": 0, "idle_minutes": 60,
    })
    _call(srv, "memory_store", {"prompt": "p", "response": "r"})
    timer = srv._idle_timer
    assert timer is not None
    autosave_calls: list[dict] = []
    monkeypatch.setattr(srv, "_maybe_autosave",
                        lambda reason, force=False: autosave_calls.append({"reason": reason, "force": force}) or None)
    srv._shutdown()
    # threading.Timer.cancel() sets .finished even if the thread hasn't joined yet
    assert timer.finished.is_set()
    assert autosave_calls and autosave_calls[0]["force"] is True


def test_autosave_disabled_skips_triggers(srv, monkeypatch):
    fired: list[str] = []
    monkeypatch.setattr(srv, "_spawn_autosave", lambda reason: fired.append(reason))
    srv.project.config.setdefault("memory", {}).setdefault("auto_save", {})
    srv.project.config["memory"]["auto_save"]["enabled"] = False
    for _ in range(5):
        _call(srv, "memory_store", {"prompt": "p", "response": "r"})
    assert fired == []
    assert srv._idle_timer is None


# --- error handling ------------------------------------------------------

def test_unknown_tool_returns_text(srv):
    text = _call(srv, "does_not_exist", {})
    assert "Unknown tool" in text


def test_handler_exception_is_caught(srv):
    # memory_invalidate without id key → KeyError inside handler → caught
    text = _call(srv, "memory_invalidate", {})
    assert "Error" in text


def test_record_decision_warning_on_invalid_supersedes(srv):
    raw = _call(srv, "record_decision", {"decision": "New decision", "supersedes": 9999})
    data = json.loads(raw)
    assert data["id"] is not None
    assert "warning" in data
    assert "supersedes id 9999 not found" in data["warning"]


def test_memory_store_global_surfaces_in_a_different_project(srv):
    """Görev 14: a scope='global' store shows up when recalling from a
    completely separate project pointed at the same ~/.cairn/global.db."""
    _call(srv, "memory_store", {
        "prompt": "coding preference", "response": "always use TypeScript",
        "scope": "global",
    })
    out = _call(srv, "memory_recall", {"query": "coding preference"})
    assert "always use TypeScript" in out
    assert "· global" in out

    import tempfile
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as other_td:
        other_project = Path(other_td)
        cfg_dir = other_project / ".cairn"
        cfg_dir.mkdir()
        global_db_path = srv.project.memory.global_db_path
        (cfg_dir / "config.yaml").write_text(
            f"memory:\n  db_path: {other_td}/mem.db\n  async_store: false\n"
            f"  global_db_path: {global_db_path}\n",
            encoding="utf-8",
        )
        other_srv = CairnMCPServer(project_dir=other_project)
        other_out = _call(other_srv, "memory_recall", {"query": "coding preference"})
        assert "always use TypeScript" in other_out


def test_memory_thread_explicit_id_and_topic(srv):
    a = json.loads(_call(srv, "record_decision", {"decision": "Use localStorage for JWT"}))["id"]
    b = json.loads(_call(srv, "record_decision",
                         {"decision": "Use httpOnly cookies for JWT", "supersedes": a}))["id"]
    chain = json.loads(_call(srv, "memory_thread", {"id": a}))
    assert [r["id"] for r in chain] == [a, b]

    matches = json.loads(_call(srv, "memory_thread", {"topic": "localStorage"}))
    assert any("localStorage" in r["decision"] for r in matches)


# --- tool profiles (mcp.tools) ------------------------------------------

def _list(server):
    resp = server._handle({"jsonrpc": "2.0", "id": 9, "method": "tools/list"})
    return resp["result"]["tools"]


def _set_profile(server, value):
    cfg = server.project_dir / ".cairn" / "config.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + (f"mcp:\n  tools: {value}\n" if value else ""), encoding="utf-8")


def test_default_profile_advertises_every_tool(srv):
    assert len(_list(srv)) == 14 == len(srv._tools)


def test_core_profile_advertises_only_the_decision_tools(srv):
    from cairn.mcp.server import CORE_TOOLS
    _set_profile(srv, "core")
    assert sorted(t["name"] for t in _list(srv)) == sorted(CORE_TOOLS)
    assert {"record_decision", "check_approach", "decisions_for_path", "memory_save"} <= set(CORE_TOOLS)


@pytest.mark.parametrize("value", ["all", "everything", "", "CORE", "[1]"])
def test_other_values_mean_all(srv, value):
    _set_profile(srv, value)
    assert len(_list(srv)) == 14


def test_a_tool_left_out_of_core_is_still_callable_by_name(srv):
    _set_profile(srv, "core")
    assert "memory_recent" not in {t["name"] for t in _list(srv)}
    resp = srv._handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "memory_recent", "arguments": {"n": 1}}})
    assert "content" in resp["result"]


def test_names_and_required_parameters_did_not_change(srv):
    by_name = {t["name"]: t["inputSchema"] for t in srv._tools}
    assert by_name["record_decision"]["required"] == ["decision"]
    assert set(by_name["record_decision"]["properties"]) == {
        "decision", "reasoning", "alternatives", "evidence", "supersedes", "replaces", "chose_because",
        "importance", "scope_path", "scope", "forbid", "valid_from"}
    assert by_name["memory_store"]["required"] == ["prompt", "response"]
    assert set(by_name["memory_save"]["properties"]) == {"summary", "next_step"}
    assert set(by_name["memory_thread"]["properties"]) == {"id", "topic", "topic_or_id", "as_of", "limit"}
    assert by_name["memory_boot"]["properties"]["format"]["enum"] == ["text", "claude", "json"]


def test_the_tool_definitions_stay_small(srv):
    """Descriptions go out with every request of every host. A rough ceiling (about 4 characters per token) that fails
    when someone grows them again: 14 tools under 7,200 characters, the core 6 under 4,200."""
    from cairn.mcp.server import CORE_TOOLS
    size = lambda tools: sum(len(json.dumps(t, ensure_ascii=False)) for t in tools)   # noqa: E731
    assert size(srv._tools) < 7200
    assert size([t for t in srv._tools if t["name"] in CORE_TOOLS]) < 4200


def test_init_writes_the_core_profile_for_new_projects():
    from cairn.interface.commands.init import PROFILE_TEMPLATES
    import yaml
    for text in PROFILE_TEMPLATES.values():
        assert yaml.safe_load(text)["mcp"]["tools"] == "core"
