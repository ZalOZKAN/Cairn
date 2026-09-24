from __future__ import annotations

import tempfile
from pathlib import Path

from cairn.memory.memory import Memory, resolve_db_path


def _project(root: Path) -> Memory:
    (root / ".cairn").mkdir()
    return Memory(
        {"memory": {"async_store": False, "global_enabled": False}},
        project_dir=root,
    )


def test_decisions_do_not_leak_between_projects():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as a, \
         tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as b:
        ma, mb = _project(Path(a)), _project(Path(b))
        ma.record_decision("Use postgres for billing storage", reasoning="acid")
        assert ma.db_path == Path(a) / ".cairn" / "persistence" / "memory.db"
        assert "postgres" in ma.recall("postgres billing storage")
        assert "postgres" not in mb.recall("postgres billing storage")


def test_explicit_db_path_wins_and_relative_resolves_to_project():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        root = Path(d)
        (root / ".cairn").mkdir()
        assert resolve_db_path({"db_path": "data/m.db"}, root) == root / "data" / "m.db"
        absolute = root / "x.db"
        assert resolve_db_path({"db_path": str(absolute)}, root) == absolute


def test_no_cairn_folder_falls_back_to_home_db():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        assert resolve_db_path({}, Path(d)) == Path("~/.cairn/memory.db").expanduser()


def test_decision_export_goes_to_project_dir_not_cwd():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        root = Path(d)
        m = _project(root)
        m.record_decision("Adopt ruff for linting")
        assert len(list((root / ".cairn" / "decisions").glob("*-adopt-ruff-for-linting.md"))) == 1


def test_init_gitignore_keeps_decisions_committable():
    from cairn.interface.commands.init import GITIGNORE_TEMPLATE
    lines = GITIGNORE_TEMPLATE.splitlines()
    assert "persistence/memory.db" in lines
    assert "memory/" not in lines
