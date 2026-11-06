"""A handoff note records the commit it was written at, says how many commits came after, and keeps a "did not work" part."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest

from cairn.memory import handoff


def git(root, *args):
    subprocess.run(["git", "-C", str(root), "-c", "user.email=k@k", "-c", "user.name=k", *args], check=True, capture_output=True)


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        root = Path(d)
        git(root, "init", "-q", "-b", "main")
        (root / "a.txt").write_text("1", encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "one")
        yield root


def test_the_working_tree_part_names_the_commit(repo):
    text = handoff.working_tree(repo)
    assert handoff.COMMIT_LINE.search(text)


def test_commits_since_counts_only_what_came_after(repo):
    note = "# Handoff\n\n" + handoff.working_tree(repo) + "\n"
    assert handoff.commits_since(repo, note) is None
    (repo / "b.txt").write_text("2", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "two")
    got = handoff.commits_since(repo, note)
    assert got and got.startswith("1 commit(s) landed since this note was written")
    assert handoff.commits_since(repo, "no commit line here") is None
    assert handoff.commits_since(repo, "Written at commit: 0000000") is None            # an unknown commit is not guessed at


def test_a_did_not_work_section_is_kept_through_a_refresh(repo):
    (repo / ".cairn" / "persistence").mkdir(parents=True)
    path = repo / ".cairn" / "persistence" / "session-handoff.md"
    path.write_text("# Handoff\n\nNote.\n", encoding="utf-8")

    class Memory:
        project_dir = repo
        db_path = None

    original = handoff.recent_decisions
    handoff.recent_decisions = lambda memory, n=5: "None recorded yet."
    try:
        text = handoff._replace_section(path.read_text(encoding="utf-8"), handoff.NOT_WORKING_HEADER, "Tried bazel: too slow")
        path.write_text(text, encoding="utf-8")
        handoff.refresh(Memory(), path)
    finally:
        handoff.recent_decisions = original
    out = path.read_text(encoding="utf-8")
    assert "## Did Not Work\nTried bazel: too slow" in out and "Written at commit:" in out
