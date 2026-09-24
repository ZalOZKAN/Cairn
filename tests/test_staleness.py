from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from cairn.memory.memory import Memory

_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, env=_ENV)


def _commit_to(root: Path, rel: str, n: int) -> None:
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        f.write_text(str(i), encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", f"c{i}")


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        root = Path(d)
        _git(root, "init", "-q")
        _commit_to(root, "README", 1)
        (root / ".cairn").mkdir()
        yield root


def _mem(root: Path, commits: int = 3) -> Memory:
    return Memory(
        {"memory": {"async_store": False, "global_enabled": False, "stale": {"commits": commits}}},
        project_dir=root,
    )


def test_decision_goes_stale_after_commits_to_its_path(repo):
    m = _mem(repo)
    mid = m.record_decision("Use SQLAlchemy core in the api", scope_path="apps/api")
    assert m.staleness(mid) is None
    _commit_to(repo, "apps/api/db.py", 3)
    st = m.staleness(mid)
    assert st and st["commits_since"] == 3 and st["path"] == "apps/api"
    assert [r["id"] for r in m.stale_decisions()] == [mid]
    assert "NEEDS REVIEW" in m.recall("sqlalchemy core api")


def test_commits_elsewhere_do_not_count(repo):
    m = _mem(repo)
    mid = m.record_decision("Use SQLAlchemy core in the api", scope_path="apps/api")
    _commit_to(repo, "apps/web/page.tsx", 5)
    assert m.staleness(mid) is None


def test_confirm_reanchors_and_updates_file(repo):
    m = _mem(repo)
    mid = m.record_decision("Use SQLAlchemy core in the api", scope_path="apps/api")
    _commit_to(repo, "apps/api/db.py", 3)
    assert m.confirm_decision(mid)
    assert m.staleness(mid) is None
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    text = next((repo / ".cairn" / "decisions").glob("*.md")).read_text(encoding="utf-8")
    assert head in text


def test_repo_wide_and_non_git_decisions_never_flagged(repo):
    m = _mem(repo)
    mid = m.record_decision("Prefer composition over inheritance")
    _commit_to(repo, "src/a.py", 5)
    assert m.staleness(mid) is None
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        (Path(d) / ".cairn").mkdir()
        plain = _mem(Path(d))
        pid = plain.record_decision("Use tabs", scope_path="src")
        assert plain.staleness(pid) is None
        with pytest.raises(ValueError):
            plain.confirm_decision(pid)
