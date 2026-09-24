"""Memory tests: decay, dedup, sliding window, async, temporal."""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from cairn.config.loader import load_config
from cairn.memory.memory import Memory
from cairn.memory.decay import decayed_score, days_old


def _mk_memory(tmpdir: str, **overrides) -> Memory:
    config = load_config()
    config["memory"]["db_path"] = f"{tmpdir}/test.db"
    # Isolate global memory to the temp dir too, never touch the real
    # ~/.cairn/global.db while running tests.
    config["memory"]["global_db_path"] = overrides.pop("global_db_path", f"{tmpdir}/global.db")
    config["memory"]["global_enabled"] = overrides.pop("global_enabled", True)
    config["memory"].setdefault("dedup", {})["enabled"] = overrides.pop("dedup", True)
    config["memory"]["async_store"] = overrides.pop("async_store", False)
    config["memory"]["backend"] = overrides.pop("backend", "sqlite")
    config["memory"].setdefault("decay", {})["enabled"] = overrides.pop("decay", True)
    return Memory(config)


# --- Decay ---------------------------------------------------------------

def test_decay_recent_is_full():
    now_ts = datetime.now(timezone.utc).isoformat()
    assert decayed_score(2, now_ts, half_life_days=30) == pytest.approx(2.0, rel=0.01)


def test_decay_one_half_life_halves():
    past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    score = decayed_score(2, past, half_life_days=30)
    assert score == pytest.approx(1.0, rel=0.05)


def test_decay_zero_half_life_disables():
    past = (datetime.now(timezone.utc) - timedelta(days=300)).isoformat()
    assert decayed_score(5, past, half_life_days=0) == 5.0


def test_days_old_bad_timestamp_returns_zero():
    assert days_old("not-a-date") == 0.0


# --- Async store + flush -------------------------------------------------

def test_async_store_flush_makes_data_visible():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir, async_store=True, dedup=False)
        try:
            mem.store("async prompt one", "async response one")
            mem.flush()
            assert any("async prompt one" in r["prompt"] for r in mem.recent(5))
        finally:
            mem.close()


# --- Dedup ---------------------------------------------------------------

def test_hash_dedup_merges_identical_entries():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        mem.store("same prompt", "same response")
        mem.store("same prompt", "same response")
        mem.store("same prompt", "same response")
        # Single row, but importance should be bumped (verify in the DB)
        import sqlite3
        with sqlite3.connect(mem.db_path) as conn:
            count, imp = conn.execute(
                "SELECT COUNT(*), MAX(importance) FROM memories"
            ).fetchone()
        assert count == 1
        assert imp >= 3


def test_dedup_disabled_keeps_duplicates():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir, dedup=False)
        mem.store("dup prompt", "dup response")
        mem.store("dup prompt", "dup response")
        import sqlite3
        with sqlite3.connect(mem.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        assert count == 2


# --- Sliding window / recall scoring -------------------------------------

def test_recall_prefers_recent_on_tie():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        # Same keyword overlap, different ages
        mem.store("alpha beta gamma topic one", "response old")
        for _ in range(5):
            mem.store("filler one " + str(_), "filler resp " + str(_))
        mem.store("alpha beta gamma topic two", "response new")
        out = mem.recall("alpha beta gamma topic")
        assert "response new" in out


# --- Temporal / invalidate -----------------------------------------------

def test_invalidate_hides_from_recall():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        mem.store("invalidate target prompt", "should disappear")
        import sqlite3
        with sqlite3.connect(mem.db_path) as conn:
            row_id = conn.execute("SELECT id FROM memories LIMIT 1").fetchone()[0]
        assert mem.invalidate(row_id, reason="outdated") is True
        # Must not appear in recall output
        out = mem.recall("invalidate target prompt")
        assert "should disappear" not in out
        # Must not appear in search either
        assert mem.search("invalidate") == []


def test_invalidate_unknown_id_returns_false():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        assert mem.invalidate(99999, reason="nope") is False


# --- Schema migration ----------------------------------------------------

def test_migration_adds_v03_columns_to_legacy_schema():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        import sqlite3
        # Set up the legacy schema manually
        db = f"{tmpdir}/legacy.db"
        with sqlite3.connect(db) as conn:
            conn.execute("""CREATE TABLE memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                session_id TEXT,
                prompt TEXT NOT NULL,
                response TEXT NOT NULL,
                keywords TEXT,
                importance INTEGER DEFAULT 1
            )""")
            conn.commit()

        config = load_config()
        config["memory"]["db_path"] = db
        config["memory"]["async_store"] = False
        Memory(config)  # constructor triggers _migrate; side-effect is the assertion target

        with sqlite3.connect(db) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
        assert {"content_hash", "valid_from", "valid_until", "invalidated_reason"} <= cols


def test_recall_header_metadata():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        mem.store("test recall header prompt", "test recall header response")
        out = mem.recall("test recall header prompt")
        assert "[#1 · memory ·" in out
        assert "Q: test recall header prompt\nA: test recall header response" in out


def test_dedup_importance_limit_and_decision_exclusion():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        for _ in range(10):
            mem.store("limit test prompt", "limit test response")
        import sqlite3
        with sqlite3.connect(mem.db_path) as conn:
            imp = conn.execute("SELECT importance FROM memories WHERE id=1").fetchone()[0]
        assert imp == 5

        dec_id = mem.record_decision("limit test prompt", reasoning="limit test response")
        mem.store("limit test prompt", "limit test response")
        with sqlite3.connect(mem.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        assert count == 3


def test_3_letter_keyword_and_fts5():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        mem.store("use httpOnly cookies for jwt to avoid xss", "recommended for security")
        kws = mem._extract_keywords("use httpOnly cookies for jwt to avoid xss")
        assert "jwt" in kws.split(",")
        out = mem.recall("jwt cookies")
        assert "recommended for security" in out


# --- Path scoping ----------------------------------------------------------

def test_migration_adds_scope_path_column():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        import sqlite3
        with sqlite3.connect(mem.db_path) as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()}
        assert "scope_path" in cols


def test_normalize_path_strips_slashes_and_backslashes():
    assert Memory._normalize_path("/apps/api/") == "apps/api"
    assert Memory._normalize_path("apps\\api") == "apps/api"


def test_recall_path_scoping_ancestor_and_sibling():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir, dedup=False)
        mem.store("api auth scoping alpha", "api-only fact", scope_path="apps/api")
        mem.store("web auth scoping alpha", "web-only fact", scope_path="apps/web")
        mem.store("global auth scoping alpha", "repo-wide fact")

        # Scoped to apps/api: sees its own entry + the global one, not apps/web.
        out = mem.recall("auth scoping alpha", path="apps/api")
        assert "api-only fact" in out
        assert "repo-wide fact" in out
        assert "web-only fact" not in out

        # Ancestor scoping: a memory scoped to "apps" is visible from "apps/api/auth".
        mem.store("ancestor scoping alpha", "ancestor fact", scope_path="apps")
        out2 = mem.recall("ancestor scoping alpha", path="apps/api/auth")
        assert "ancestor fact" in out2

        # No path filter: everything is visible (backward compatible default).
        out3 = mem.recall("auth scoping alpha")
        assert "api-only fact" in out3 and "web-only fact" in out3


def test_search_path_scoping():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir, dedup=False)
        mem.store("search scope api topic", "api detail", scope_path="apps/api")
        mem.store("search scope web topic", "web detail", scope_path="apps/web")

        api_results = mem.search("search scope", path="apps/api")
        assert any("api detail" in r["response"] for r in api_results)
        assert not any("web detail" in r["response"] for r in api_results)


def test_record_decision_scope_path_round_trips():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        dec_id = mem.record_decision(
            "use per-service auth middleware",
            reasoning="apps/api needs its own token validation",
            scope_path="apps/api",
        )
        full = mem.get_full(dec_id)
        assert full["scope_path"] == "apps/api"
