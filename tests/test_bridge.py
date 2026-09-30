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


# ---- Gemini CLI ----------------------------------------------------------------

# What Gemini CLI 0.61.0 itself wrote to ~/.gemini/GEMINI.md when told "Remember
# that I personally always prefer 4-space indentation, not tabs, in any Python
# code I write" (live probe, 2026-09-29).
REAL_GLOBAL_SAMPLE = "- Python code preference: Always prefer 4-space indentation, not tabs.\n"


def test_the_note_gemini_really_wrote_becomes_a_global_candidate(tmp_path):
    home = tmp_path / "gemini-home"
    home.mkdir()
    (home / "GEMINI.md").write_text(REAL_GLOBAL_SAMPLE, encoding="utf-8")
    assert bridge.parse_gemini_memory(tmp_path / "proj", gemini_home=home) == [{
        "decision": "Python code preference: Always prefer 4-space indentation, not tabs.",
        "reasoning": None, "source": "gemini-global", "origin": "GEMINI.md", "scope": "global"}]


def test_gemini_project_file_skips_code_nested_bullets_and_managed_blocks(tmp_path):
    (tmp_path / "GEMINI.md").write_text(
        "# Team\n- Use pnpm, not npm\n  - since the lockfile mess\n```\n- not a fact\n```\n"
        "<!-- othertool:start -->\n- othertool's own section\n<!-- othertool:end -->\n* Never deploy on Fridays\n",
        encoding="utf-8")
    items = bridge.parse_gemini_memory(tmp_path, gemini_home=tmp_path / "no-home")
    assert [(i["decision"], i.get("scope", "project")) for i in items] == [
        ("Use pnpm, not npm", "project"), ("Never deploy on Fridays", "project")]


def test_gemini_private_project_memory_is_never_imported(tmp_path):
    home = tmp_path / "gemini-home"
    private = home / "tmp" / "proj" / "memory"
    private.mkdir(parents=True)
    (private / "MEMORY.md").write_text("- my local Postgres listens on 5433\n", encoding="utf-8")
    assert bridge.parse_gemini_memory(tmp_path / "proj", gemini_home=home) == []


def test_import_native_gemini_keeps_the_personal_note_out_of_the_repo(tmp_path):
    import os
    home, project = tmp_path / "home", tmp_path / "proj"
    (home / ".gemini").mkdir(parents=True)
    (home / ".gemini" / "GEMINI.md").write_text(REAL_GLOBAL_SAMPLE, encoding="utf-8")
    (project / ".cairn").mkdir(parents=True)
    global_db = (tmp_path / "global.db").as_posix()
    (project / ".cairn" / "config.yaml").write_text(
        f"memory: {{async_store: false, global_enabled: true, global_db_path: '{global_db}'}}\n", encoding="utf-8")
    (project / "GEMINI.md").write_text("- Use pnpm, not npm\n", encoding="utf-8")
    env = {**os.environ, "USERPROFILE": str(home), "HOME": str(home)}

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-c", CLI.format(args=list(args))], capture_output=True,
                              text=True, encoding="utf-8", cwd=project, env=env)

    out = run("memory", "import-native", "--source", "gemini", "--yes")
    assert out.returncode == 0, out.stdout + out.stderr
    assert "[gemini-global] (global) Python code preference" in out.stdout
    files = "".join(f.read_text(encoding="utf-8") for f in (project / ".cairn" / "decisions").glob("*.md"))
    assert "Use pnpm, not npm" in files and "4-space" not in files
    import sqlite3
    with sqlite3.connect(global_db) as conn:
        rows = conn.execute("SELECT prompt, recorded_by FROM memories WHERE kind='decision'").fetchall()
    assert rows == [("Python code preference: Always prefer 4-space indentation, not tabs.", "gemini-global")]
    assert "Nothing new to import" in run("memory", "import-native", "--source", "gemini", "--yes").stdout


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
