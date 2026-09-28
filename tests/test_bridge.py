"""Memory bridge: importing another tool's own memory as Cairn decisions."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from cairn.memory import bridge
from cairn.memory.memory import Memory

CLI = "import sys; sys.argv=['cairn', *{args!r}]; from cairn.interface.cli import main; main()"


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        r = Path(d)
        (r / ".cairn").mkdir()
        (r / ".cairn" / "config.yaml").write_text(
            "memory: {async_store: false, global_enabled: false}\n", encoding="utf-8")
        yield r


def _mem(root: Path) -> Memory:
    return Memory({"memory": {"async_store": False, "global_enabled": False}}, project_dir=root)


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", CLI.format(args=list(args))],
                          capture_output=True, text=True, encoding="utf-8", cwd=root)


# ---- Claude Code auto-memory ---------------------------------------------

def test_claude_project_path_is_encoded_like_the_real_tool():
    assert (bridge.claude_memory_dir(Path("C:/Projects/Cairn"), claude_home=Path("H"))
           == Path("H") / "projects" / "C--Projects-Cairn" / "memory")


def _write_claude_memory(base: Path) -> Path:
    mem_dir = base / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text(
        "# Memory Index\n\n"
        "- [Git rules](feedback_git_commits.md): ask before every commit, no Co-Authored-By\n"
        "- [Broken entry with no matching file](missing.md): should be skipped\n"
        "not a bullet at all, ignored\n",
        encoding="utf-8",
    )
    (mem_dir / "feedback_git_commits.md").write_text(
        "---\n"
        "name: feedback-git-commits\n"
        'description: "Cairn git rules"\n'
        "metadata:\n"
        "  type: feedback\n"
        "---\n\n"
        "Never commit without asking.\n\n"
        "**Why:** the user asked for this explicitly after an unwanted commit.\n\n"
        "**How to apply:** every git command in this repo.\n",
        encoding="utf-8",
    )
    return mem_dir


def test_parse_claude_memory_reads_the_index_hook_and_why_section(tmp_path):
    mem_dir = _write_claude_memory(tmp_path)
    items = bridge.parse_claude_memory(mem_dir)
    assert len(items) == 1  # the broken index line and the non-bullet line are both skipped
    item = items[0]
    assert item["decision"] == "ask before every commit, no Co-Authored-By"
    assert item["reasoning"] == "the user asked for this explicitly after an unwanted commit."
    assert item["source"] == "claude-code-memory" and item["origin"] == "feedback_git_commits.md"


def test_parse_claude_memory_missing_folder_is_empty_not_an_error(tmp_path):
    assert bridge.parse_claude_memory(tmp_path / "does-not-exist") == []


# ---- Cursor rules ---------------------------------------------------------

def test_parse_cursor_rules_only_imports_always_apply(tmp_path):
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "always.md").write_text(
        "---\ndescription: Use pnpm, never npm, for every install in this repo\nalwaysApply: true\n---\n\nbody\n",
        encoding="utf-8")
    (rules / "scoped.md").write_text(
        "---\ndescription: React component conventions\nglobs: '**/*.tsx'\nalwaysApply: false\n---\n\nbody\n",
        encoding="utf-8")
    (rules / "no-front-matter.md").write_text("Always run tests before committing.\n", encoding="utf-8")
    items = bridge.parse_cursor_rules(tmp_path)
    decisions = {i["decision"] for i in items}
    assert "Use pnpm, never npm, for every install in this repo" in decisions
    assert "Always run tests before committing." in decisions
    assert not any("React component" in d for d in decisions)
    assert all(i["source"] == "cursor-rules" for i in items)


def test_parse_cursor_rules_missing_folder_is_empty(tmp_path):
    assert bridge.parse_cursor_rules(tmp_path) == []


# ---- Gemini CLI is not a source -----------------------------------------

def test_gemini_is_not_an_import_source(root):
    """The parser for a `## Gemini Added Memories` heading (which the real CLI
    does not have) was removed, not kept as a silent no-op."""
    assert "gemini" not in bridge.PARSERS
    out = _cli(root, "memory", "import-native", "--source", "gemini", "--yes")
    assert out.returncode == 1 and "Unknown source 'gemini'" in out.stdout


# ---- candidates() dedup + CLI ---------------------------------------------

def test_candidates_dedups_against_already_recorded_text_but_not_edited_text(root, monkeypatch):
    mem_dir = _write_claude_memory(root)
    monkeypatch.setattr(bridge, "PARSERS", {"claude": lambda p: bridge.parse_claude_memory(mem_dir)})
    m = _mem(root)
    first = bridge.candidates(root)
    assert len(first) == 1
    m.record_decision(first[0]["decision"], reasoning=first[0]["reasoning"], recorded_by=first[0]["source"])
    assert bridge.candidates(root) == []  # unchanged text: already imported

    (mem_dir / "feedback_git_commits.md").write_text(
        (mem_dir / "feedback_git_commits.md").read_text(encoding="utf-8")
        .replace("Never commit without asking.", "Never commit without asking, ever."),
        encoding="utf-8")
    (mem_dir / "MEMORY.md").write_text(
        (mem_dir / "MEMORY.md").read_text(encoding="utf-8")
        .replace("ask before every commit, no Co-Authored-By", "ask before every commit, always"),
        encoding="utf-8")
    changed = bridge.candidates(root)
    assert len(changed) == 1  # the hook text changed, so it is offered again


def _write_cursor_rule(root: Path) -> None:
    rules = root / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "always.md").write_text(
        "---\ndescription: Use pnpm, never npm, for every install in this repo\nalwaysApply: true\n---\n\nbody\n",
        encoding="utf-8")


def test_import_native_cli_shows_candidates_and_records_on_yes(root):
    _write_cursor_rule(root)
    out = _cli(root, "memory", "import-native", "--source", "cursor", "--yes")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "Imported 1 decision" in out.stdout
    files = list((root / ".cairn" / "decisions").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "Use pnpm" in text and "recorded_by: cursor-rules" in text
    # re-running finds nothing new
    again = _cli(root, "memory", "import-native", "--source", "cursor", "--yes")
    assert "Nothing new to import" in again.stdout


def test_import_native_cli_cancelled_writes_nothing(root):
    _write_cursor_rule(root)
    out = subprocess.run(
        [sys.executable, "-c", CLI.format(args=["memory", "import-native", "--source", "cursor"])],
        input="n\n", capture_output=True, text=True, encoding="utf-8", cwd=root)
    assert out.returncode == 0 and "Cancelled" in out.stdout
    assert _mem(root).recent(n=5) == []


def test_unknown_source_is_rejected(root):
    out = _cli(root, "memory", "import-native", "--source", "notarealtool")
    assert out.returncode == 1 and "Unknown source" in out.stdout


def test_import_native_cli_declines_cleanly_on_closed_stdin(root):
    """A closed/absent stdin (no TTY: CI, or a script that pipes nothing) makes
    typer.confirm raise Abort rather than return its default; that must not
    turn into a non-zero exit, only into the same clean cancel as a real 'n'."""
    _write_cursor_rule(root)
    out = subprocess.run(
        [sys.executable, "-c", CLI.format(args=["memory", "import-native", "--source", "cursor"])],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", cwd=root)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "Cancelled" in out.stdout
    assert _mem(root).recent(n=5) == []
