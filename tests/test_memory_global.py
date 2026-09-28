"""Global (cross-project) memory tests, Görev 14."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cairn.config.loader import load_config
from cairn.memory.memory import Memory


def _mk_memory(tmpdir: str, **overrides) -> Memory:
    config = load_config()
    config["memory"]["db_path"] = f"{tmpdir}/test.db"
    config["memory"]["global_db_path"] = overrides.pop("global_db_path", f"{tmpdir}/global.db")
    config["memory"]["global_enabled"] = overrides.pop("global_enabled", True)
    config["memory"].setdefault("dedup", {})["enabled"] = overrides.pop("dedup", False)
    config["memory"]["async_store"] = overrides.pop("async_store", False)
    config["memory"]["backend"] = overrides.pop("backend", "sqlite")
    config["memory"].setdefault("decay", {})["enabled"] = overrides.pop("decay", True)
    return Memory(config, project_dir=Path(tmpdir))


def test_global_store_creates_separate_db_file():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        mem.store("prefer typescript", "user prefers TypeScript over JS", scope="global")
        import sqlite3
        with sqlite3.connect(mem.global_db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        assert count == 1
        with sqlite3.connect(mem.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        assert count == 0


def test_recall_merges_global_into_project_scoped_query():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        mem.store("project-only detail alpha", "lives in this project", scope="project")
        mem.store("global preference alpha", "user prefers TypeScript", scope="global")
        out = mem.recall("alpha")
        assert "lives in this project" in out
        assert "user prefers TypeScript" in out
        assert "· global" in out


def test_global_store_visible_from_a_different_fake_project():
    """The whole point: a fact stored globally surfaces in an unrelated project's recall."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as shared_global:
        global_db_path = f"{shared_global}/global.db"

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as project_a:
            mem_a = _mk_memory(project_a, global_db_path=global_db_path)
            mem_a.store(
                "commit message convention",
                "use Conventional Commits format",
                scope="global",
            )

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as project_b:
            mem_b = _mk_memory(project_b, global_db_path=global_db_path)
            out = mem_b.recall("commit message convention")
            assert "Conventional Commits" in out


def test_global_disabled_makes_global_scope_raise():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir, global_enabled=False)
        with pytest.raises(ValueError):
            mem.store("x", "y", scope="global")


def test_global_disabled_recall_has_no_global_tag():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir, global_enabled=False)
        mem.store("project fact beta", "project-scoped only", scope="project")
        out = mem.recall("fact beta")
        assert "project-scoped only" in out
        assert "· global" not in out


def test_invalid_scope_raises():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        with pytest.raises(ValueError):
            mem.store("x", "y", scope="nonsense")


def test_g_prefixed_id_round_trips_through_get_full_and_invalidate():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        new_id = mem.record_decision("prefer pnpm over npm", reasoning="faster installs", scope="global")
        assert isinstance(new_id, str) and new_id.startswith("g")

        full = mem.get_full(new_id)
        assert full is not None
        assert full["decision"] == "prefer pnpm over npm"
        assert full["scope"] == "global"
        assert full["id"] == new_id

        assert mem.invalidate(new_id, reason="changed my mind") is True
        assert mem.get_full(new_id)["valid_until"] is not None

        # Project-scoped id namespace is untouched by the global invalidate.
        assert mem.invalidate(99999) is False


def test_thread_walks_global_supersedes_chain():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        a = mem.record_decision("use npm", scope="global")
        b = mem.record_decision("use pnpm", supersedes=a, scope="global")
        chain = mem.thread(a)
        assert [r["id"] for r in chain] == [a, b]
        assert chain[0]["scope"] == "global"
        assert chain[-1]["active"] is True


def test_search_merges_global_results():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        mem.store("search gamma project", "project detail", scope="project")
        mem.store("search gamma global", "global detail", scope="global")
        results = mem.search("search gamma")
        scopes = {r["scope"] for r in results}
        assert scopes == {"project", "global"}
