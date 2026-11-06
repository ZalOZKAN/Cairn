"""The inbox, lessons and correction catcher through the real command line and hooks."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        r = Path(d)
        (r / ".cairn").mkdir()
        (r / ".cairn" / "config.yaml").write_text("memory: {async_store: false, global_enabled: false}\n", encoding="utf-8")
        yield r


def cairn(root: Path, *args: str, stdin: str = "") -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(REPO), PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-m", "cairn", *args], cwd=root, input=stdin, capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=120)


def test_a_correction_in_a_prompt_lands_in_the_inbox_and_can_be_confirmed(root):
    payload = json.dumps({"cwd": str(root), "prompt": "no, use pytest instead of unittest", "hook_event_name": "UserPromptSubmit"})
    assert cairn(root, "memory", "detect", stdin=payload).returncode == 0
    listed = cairn(root, "memory", "inbox").stdout
    assert "Use pytest instead of unittest" in listed and "evidence: no, use pytest instead of unittest" in listed
    item_id = listed.split()[0]
    assert cairn(root, "memory", "check", "unittest").stdout.count("No record") == 1          # not a decision yet
    done = cairn(root, "memory", "inbox", "--confirm", item_id)
    assert done.returncode == 0 and "Recorded as decision" in done.stdout
    assert "The inbox is empty" in cairn(root, "memory", "inbox").stdout
    assert "pytest" in cairn(root, "memory", "recent").stdout                                  # now it is a real decision


def test_dismissing_keeps_the_same_correction_out(root):
    payload = json.dumps({"cwd": str(root), "prompt": "don't use moment.js"})
    cairn(root, "memory", "detect", stdin=payload)
    item_id = cairn(root, "memory", "inbox").stdout.split()[0]
    assert cairn(root, "memory", "inbox", "--dismiss", item_id).returncode == 0
    cairn(root, "memory", "detect", stdin=payload)
    assert "The inbox is empty" in cairn(root, "memory", "inbox").stdout
    assert cairn(root, "memory", "inbox", "--dismiss", "20260101-abcdef").returncode == 1


def test_a_lesson_is_shown_by_the_path_hook_only_for_its_file(root):
    (root / "src").mkdir()
    (root / "src" / "parser.py").write_text("x = 1\n", encoding="utf-8")
    stored = cairn(root, "memory", "lesson", "The parser needs a trailing newline", "--file", "src/parser.py", "--origin", "user-stated")
    assert stored.returncode == 0 and "stored" in stored.stdout
    hook = json.dumps({"cwd": str(root), "tool_input": {"file_path": str(root / "src" / "parser.py")}})
    shown = cairn(root, "memory", "for-path", "--hook", stdin=hook).stdout
    assert "trailing newline" in shown
    other = json.dumps({"cwd": str(root), "tool_input": {"file_path": str(root / "src" / "other.py")}})
    assert cairn(root, "memory", "for-path", "--hook", stdin=other).stdout.strip() == ""
    lesson_id = cairn(root, "memory", "lessons").stdout.split()[0]
    assert cairn(root, "memory", "lessons", "--forget", lesson_id).returncode == 0
    assert "No lessons" in cairn(root, "memory", "lessons").stdout


def test_the_session_context_mentions_waiting_proposals(root):
    cairn(root, "memory", "detect", stdin=json.dumps({"cwd": str(root), "prompt": "lodash kullanma"}))
    out = cairn(root, "memory", "context").stdout
    assert "Cairn inbox: 1 proposed decision" in out


def test_learn_without_a_helper_model_does_nothing_and_never_fails_a_hook(root):
    transcript = root / "t.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {"content": "x" * 3000}}) + "\n", encoding="utf-8")
    hook = json.dumps({"cwd": str(root), "transcript_path": str(transcript), "session_id": "s", "hook_event_name": "Stop"})
    assert cairn(root, "memory", "learn", "--hook", stdin=hook).returncode == 0
    assert cairn(root, "memory", "inbox").stdout.strip() == "The inbox is empty."
