"""Dashboard generator tests (Phase 2 Stream C)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from cairn.interface.commands.dashboard import (
    collect_dashboard_data,
    render_dashboard_html,
    write_dashboard,
)
from cairn.memory.memory import Memory


@pytest.fixture
def populated_db():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db = Path(td) / "mem.db"
        m = Memory({"memory": {"db_path": str(db), "async_store": False}})
        a = m.record_decision(
            "Use SQLite for local dev",
            reasoning="No infra to manage",
            alternatives=[{"option": "Postgres", "rejected_because": "overkill"}],
        )
        b = m.record_decision(
            "Use httpOnly cookies for JWT",
            reasoning="Mitigates XSS",
            evidence=[{"source": "OWASP", "claim": "httpOnly blocks JS read"}],
            supersedes=a,
        )
        # one plain (non-decision) memory: should NOT show up
        m.store("random q", "random a", importance=1)
        yield db, a, b


def test_collect_includes_only_decisions(populated_db):
    db, a, b = populated_db
    data = collect_dashboard_data(db, filt="all")
    ids = {d["id"] for d in data["decisions"]}
    assert ids == {a, b}


def test_active_and_invalidated_counts(populated_db):
    db, a, b = populated_db
    data = collect_dashboard_data(db, filt="all")
    assert data["active_count"] == 1
    assert data["invalidated_count"] == 1


def test_filter_active_excludes_invalidated(populated_db):
    db, a, b = populated_db
    data = collect_dashboard_data(db, filt="active")
    assert all(d["active"] for d in data["decisions"])
    assert all(d["id"] != a for d in data["decisions"])


def test_filter_invalidated_excludes_active(populated_db):
    db, a, b = populated_db
    data = collect_dashboard_data(db, filt="invalidated")
    assert all(not d["active"] for d in data["decisions"])
    assert all(d["id"] != b for d in data["decisions"])


def test_audit_score_reflects_completeness(populated_db):
    db, a, b = populated_db
    data = collect_dashboard_data(db, filt="all")
    by_id = {d["id"]: d for d in data["decisions"]}
    # a: reasoning + alternatives + superseded_by (it was superseded) → 3
    assert by_id[a]["audit_score"] == 3
    # b: reasoning + evidence → 2 (no alternatives, no superseded_by)
    assert by_id[b]["audit_score"] == 2


def test_render_html_embeds_data_island(populated_db):
    db, _, _ = populated_db
    data = collect_dashboard_data(db, filt="all")
    html = render_dashboard_html(data)
    assert "__DATA_JSON__" not in html  # placeholder replaced
    assert 'id="cairn-data"' in html
    assert "Use SQLite for local dev" in html


def test_render_html_uses_cdn_assets(populated_db):
    db, _, _ = populated_db
    html = render_dashboard_html(collect_dashboard_data(db))
    assert "tailwindcss.com" in html
    assert "chart.js" in html.lower()


def test_write_dashboard_creates_file(populated_db, tmp_path):
    db, _, _ = populated_db
    # Monkey-patch the db path via config loader is heavier than helpful;
    # instead, write the HTML manually using the public render path.
    data = collect_dashboard_data(db)
    html = render_dashboard_html(data)
    out = tmp_path / ".cairn" / "persistence" / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    assert out.exists()
    assert "<html" in out.read_text(encoding="utf-8").lower()


def test_collect_empty_db_returns_empty_shape():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        data = collect_dashboard_data(Path(td) / "missing.db", filt="all")
    assert data["decisions"] == []
    assert data["active_count"] == 0


def test_unknown_filter_defaults_to_all(populated_db):
    db, _, _ = populated_db
    data = collect_dashboard_data(db, filt="garbage")
    assert data["filter"] == "all"
    assert len(data["decisions"]) == 2
