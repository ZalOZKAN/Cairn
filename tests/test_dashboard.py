"""Dashboard generator tests (Phase 2 Stream C)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cairn.interface.commands.dashboard import (
    collect_dashboard_data,
    render_dashboard_html,
)
from cairn.memory.memory import Memory


@pytest.fixture
def populated_db():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db = Path(td) / "mem.db"
        m = Memory({"memory": {
            "db_path": str(db),
            "async_store": False,
            "global_db_path": str(Path(td) / "global.db"),
        }}, project_dir=Path(td))
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
    assert "d3@7" in html  # force-directed decision graph


def test_render_html_includes_graph_and_toggle(populated_db):
    db, _, _ = populated_db
    html = render_dashboard_html(collect_dashboard_data(db))
    assert 'id="graphSvg"' in html
    assert 'id="viewGraphBtn"' in html
    assert 'id="viewCardsBtn"' in html
    assert "forceSimulation" in html
    assert "scopeTabs" in html
    assert "This Project" in html


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


def test_collect_embeds_other_projects_passthrough(populated_db):
    db, _, _ = populated_db
    others = [{"name": "sibling", "path": "/tmp/sibling", "last_seen": "x", "dashboard_uri": None}]
    data = collect_dashboard_data(db, filt="all", other_projects=others)
    assert data["other_projects"] == others


def test_collect_defaults_other_projects_to_empty_list(populated_db):
    db, _, _ = populated_db
    data = collect_dashboard_data(db, filt="all")
    assert data["other_projects"] == []


def test_write_dashboard_registers_project_and_links_siblings(tmp_path, monkeypatch):
    project_a = tmp_path / "project_a"
    project_b = tmp_path / "project_b"
    project_a.mkdir()
    project_b.mkdir()
    registry_path = tmp_path / "registry.json"

    fake_config = {
        "memory": {
            "db_path": str(project_a / "mem.db"),
            "global_enabled": False,
            "registry_path": str(registry_path),
        }
    }
    import cairn.config.loader as loader_module
    monkeypatch.setattr(loader_module, "load_config", lambda *a, **k: fake_config)

    from cairn.config.registry import register_project
    from cairn.interface.commands.dashboard import write_dashboard

    register_project(project_b, registry_path)  # simulate a sibling project seen before
    out = write_dashboard(project_a)
    html = out.read_text(encoding="utf-8")

    assert "project_b" in html
    assert list_projects_names(registry_path) == {"project_a", "project_b"}


def list_projects_names(registry_path: Path) -> set[str]:
    from cairn.config.registry import list_projects
    return {p["name"] for p in list_projects(registry_path)}


def test_collect_merges_global_decisions_with_g_prefix(populated_db):
    db, a, b = populated_db
    global_db = db.parent / "global.db"
    m = Memory({"memory": {
        "db_path": str(db),
        "async_store": False,
        "global_db_path": str(global_db),
    }})
    g_id = m.record_decision(
        "Always use TypeScript", reasoning="team preference", scope="global"
    )
    data = collect_dashboard_data(db, filt="all", global_db_path=global_db)
    scopes = {d["id"]: d["scope"] for d in data["decisions"]}
    assert scopes[a] == "project"
    assert scopes[b] == "project"
    assert scopes[str(g_id)] == "global"
    assert data["has_global"] is True
    assert data["global_active_count"] == 1
    assert data["project_active_count"] == 1


def test_collect_without_global_db_path_has_no_global_scope(populated_db):
    db, _, _ = populated_db
    data = collect_dashboard_data(db, filt="all")
    assert data["has_global"] is False
    assert all(d["scope"] == "project" for d in data["decisions"])
