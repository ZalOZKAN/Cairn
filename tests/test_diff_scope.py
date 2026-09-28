"""cairn memory for-diff: the active decisions that apply to a diff's files."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from cairn.memory.diff_scope import changed_paths, decisions_for_diff
from cairn.memory.memory import Memory

_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
}
CLI = "import sys; sys.argv=['cairn', *{args!r}]; from cairn.interface.cli import main; main()"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True,
                          env=_ENV).stdout.strip()


def _write(root: Path, rel: str, text: str = "x") -> None:
    f = root / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")


def _mem(root: Path) -> Memory:
    return Memory({"memory": {"async_store": False, "global_enabled": False}}, project_dir=root)


@pytest.fixture
def repo():
    """A repo with a committed base (apps/api/a.py, apps/web/x.tsx, README)."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        root = Path(d)
        _git(root, "init", "-q")
        for rel in ("apps/api/a.py", "apps/web/x.tsx", "README"):
            _write(root, rel)
        _write(root, ".gitignore", "*.log\n")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "base")
        (root / ".cairn").mkdir()
        yield root


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", CLI.format(args=list(args))], capture_output=True,
                          text=True, encoding="utf-8", cwd=root, input="")


# ---- changed_paths ---------------------------------------------------------

def test_default_is_all_uncommitted_work_including_untracked_but_not_ignored(repo):
    _write(repo, "apps/api/a.py", "edited")              # modified, unstaged
    _write(repo, "apps/api/new.py")                      # untracked
    _write(repo, "debug.log")                            # ignored
    (repo / "README").unlink()                           # deleted
    _write(repo, "apps/web/staged.tsx")
    _git(repo, "add", "apps/web/staged.tsx")             # staged new file
    assert changed_paths(repo) == ["README", "apps/api/a.py", "apps/api/new.py", "apps/web/staged.tsx"]


def test_cairns_own_files_are_not_part_of_the_diff(repo):
    """Recording a decision writes .cairn/decisions/*.md, an uncommitted change
    that no decision governs; counting it would inflate every diff."""
    _mem(repo).record_decision("Use SQLAlchemy core in the api", scope_path="apps/api")
    _write(repo, "apps/api/a.py", "edited")
    assert changed_paths(repo) == ["apps/api/a.py"]


def test_staged_flag_limits_it_to_the_index(repo):
    _write(repo, "apps/api/a.py", "edited")
    _write(repo, "apps/web/staged.tsx")
    _git(repo, "add", "apps/web/staged.tsx")
    assert changed_paths(repo, staged=True) == ["apps/web/staged.tsx"]


def test_a_range_lists_what_the_commits_in_it_changed(repo):
    base = _git(repo, "rev-parse", "HEAD")
    _write(repo, "apps/api/b.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "second")
    _write(repo, "apps/web/uncommitted.tsx")
    assert changed_paths(repo, f"{base}..HEAD") == ["apps/api/b.py"]


def test_a_rename_counts_as_the_old_and_the_new_path(repo):
    _git(repo, "mv", "apps/api/a.py", "apps/web/a.py")
    assert changed_paths(repo) == ["apps/api/a.py", "apps/web/a.py"]


def test_works_before_the_first_commit(tmp_path):
    _git(tmp_path, "init", "-q")
    _write(tmp_path, "one.py")
    assert changed_paths(tmp_path) == ["one.py"]


def test_a_monorepo_subdirectory_sees_only_its_own_files_relative_to_itself(repo):
    _write(repo, "apps/api/new.py")
    _write(repo, "apps/web/other.tsx")
    assert changed_paths(repo / "apps" / "api") == ["new.py"]


def test_unreadable_diffs_give_none_not_an_empty_list(repo, tmp_path):
    assert changed_paths(tmp_path) is None                       # not a repository
    assert changed_paths(repo, "no-such-branch..HEAD") is None   # unknown revision
    assert changed_paths(repo, "--output=leak.txt") is None      # never read as a git option
    assert not (repo / "leak.txt").exists()


# ---- decisions_for_diff ----------------------------------------------------

def test_each_decision_once_with_the_changed_files_it_applies_to(repo):
    m = _mem(repo)
    api = m.record_decision("Use SQLAlchemy core in the api", scope_path="apps/api", importance=2)
    web = m.record_decision("Components are function components", scope_path="apps/web", importance=3)
    m.record_decision("Money is integer cents")            # repo-wide: left out on purpose
    rows = decisions_for_diff(m, ["apps/api/a.py", "apps/api/b.py", "apps/web/x.tsx", "README"])
    assert [r["id"] for r in rows] == [web, api]           # importance first
    by_id = {r["id"]: r["files"] for r in rows}
    assert by_id[api] == ["apps/api/a.py", "apps/api/b.py"]
    assert by_id[web] == ["apps/web/x.tsx"]


def test_no_changed_files_or_no_scoped_decisions_gives_nothing(repo):
    m = _mem(repo)
    m.record_decision("Money is integer cents")
    assert decisions_for_diff(m, []) == []
    assert decisions_for_diff(m, ["apps/api/a.py"]) == []


def test_an_invalidated_decision_no_longer_applies(repo):
    m = _mem(repo)
    mid = m.record_decision("Use SQLAlchemy core in the api", scope_path="apps/api")
    m.invalidate(mid, reason="moved to sqlmodel")
    assert decisions_for_diff(m, ["apps/api/a.py"]) == []


# ---- the command -----------------------------------------------------------

def test_command_lists_the_decisions_with_the_files_that_triggered_them(repo):
    m = _mem(repo)
    mid = m.record_decision("Use SQLAlchemy core in the api", scope_path="apps/api")
    _write(repo, "apps/api/a.py", "edited")
    _write(repo, "README", "edited")
    out = _cli(repo, "memory", "for-diff")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "your uncommitted changes (2 changed files)" in out.stdout
    assert f"#{mid} [apps/api]: Use SQLAlchemy core in the api (changed: apps/api/a.py)" in out.stdout


def test_command_says_so_when_nothing_applies(repo):
    _mem(repo).record_decision("Use SQLAlchemy core in the api", scope_path="apps/api")
    _write(repo, "apps/web/x.tsx", "edited")
    out = _cli(repo, "memory", "for-diff")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "No path-scoped decisions apply to your uncommitted changes (1 changed files)." in out.stdout
    assert "[dim]" not in out.stdout


def test_command_takes_a_range_and_reports_a_bad_one_with_a_nonzero_exit(repo):
    _mem(repo).record_decision("Use SQLAlchemy core in the api", scope_path="apps/api")
    base = _git(repo, "rev-parse", "HEAD")
    _write(repo, "apps/api/b.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "second")
    ok = _cli(repo, "memory", "for-diff", f"{base}..HEAD")
    assert ok.returncode == 0 and "(changed: apps/api/b.py)" in ok.stdout
    bad = _cli(repo, "memory", "for-diff", "nope..HEAD")
    assert bad.returncode == 1 and "Could not read the diff for nope..HEAD" in bad.stdout


def test_command_refuses_a_range_together_with_staged(repo):
    out = _cli(repo, "memory", "for-diff", "HEAD~1..HEAD", "--staged")
    assert out.returncode == 2 and "not both" in out.stdout
