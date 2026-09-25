"""Cross-project registry tests (~/.cairn/projects.json, override-tested)."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cairn.config.registry import list_projects, register_project


class _FrozenClock:
    """Every registration gets the same timestamp, like two quick calls on a
    coarse Windows clock (~15.6 ms on Python < 3.13)."""

    @staticmethod
    def now(tz=None):
        return datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_same_timestamp_ties_resolve_to_most_recently_registered(tmp_path, monkeypatch):
    monkeypatch.setattr("cairn.config.registry.datetime", _FrozenClock)
    registry_path = tmp_path / "registry.json"
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    register_project(a, registry_path)
    register_project(b, registry_path)
    assert [p["name"] for p in list_projects(registry_path)] == ["b", "a"]
    register_project(a, registry_path)  # touching a again makes it the most recent
    assert [p["name"] for p in list_projects(registry_path)] == ["a", "b"]


def test_register_creates_registry_file():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        registry_path = Path(td) / "registry.json"
        project = Path(td) / "my-project"
        project.mkdir()
        register_project(project, registry_path)
        assert registry_path.exists()
        projects = list_projects(registry_path)
        assert len(projects) == 1
        assert projects[0]["name"] == "my-project"
        assert projects[0]["path"] == str(project.resolve())


def test_register_same_project_twice_updates_not_duplicates():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        registry_path = Path(td) / "registry.json"
        project = Path(td) / "my-project"
        project.mkdir()
        register_project(project, registry_path)
        first_seen = list_projects(registry_path)[0]["last_seen"]
        register_project(project, registry_path)
        projects = list_projects(registry_path)
        assert len(projects) == 1
        assert projects[0]["last_seen"] >= first_seen


def test_list_projects_multiple_sorted_most_recent_first():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        registry_path = Path(td) / "registry.json"
        a = Path(td) / "a"
        b = Path(td) / "b"
        a.mkdir()
        b.mkdir()
        register_project(a, registry_path)
        register_project(b, registry_path)
        projects = list_projects(registry_path)
        assert {p["name"] for p in projects} == {"a", "b"}
        assert projects[0]["name"] == "b"  # registered last, seen most recently


def test_list_projects_missing_registry_returns_empty():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        registry_path = Path(td) / "missing.json"
        assert list_projects(registry_path) == []


def test_list_projects_corrupt_registry_returns_empty_and_recovers():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        registry_path = Path(td) / "registry.json"
        registry_path.write_text("not valid json{{{", encoding="utf-8")
        assert list_projects(registry_path) == []
        project = Path(td) / "my-project"
        project.mkdir()
        register_project(project, registry_path)  # overwrites the corrupt file
        assert len(list_projects(registry_path)) == 1
