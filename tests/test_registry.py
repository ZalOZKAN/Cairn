"""Cross-project registry tests (~/.cairn/projects.json, override-tested)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from cairn.config.registry import list_projects, register_project


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
