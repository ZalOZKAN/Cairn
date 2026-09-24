from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from cairn.memory.capture import edits_since_last_decision, nudge_message, propose_from_git


def _line(*names: str) -> str:
    return json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": n, "input": {}} for n in names
    ]}})


@pytest.fixture
def tmp():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        yield Path(d)


def test_edit_count_resets_on_record_decision(tmp):
    t = tmp / "t.jsonl"
    t.write_text("\n".join([
        _line("Edit", "Write"), "not json", _line("mcp__cairn__record_decision"), _line("Edit", "Read"),
    ]), encoding="utf-8")
    assert edits_since_last_decision(t) == 1


def test_nudge_fires_once_per_threshold_and_respects_stop_hook_active(tmp):
    t = tmp / "t.jsonl"
    state = tmp / "state.json"
    t.write_text(_line(*["Edit"] * 3), encoding="utf-8")
    payload = {"transcript_path": str(t), "session_id": "s1"}
    assert nudge_message(payload, state, threshold=3) is not None
    assert nudge_message(payload, state, threshold=3) is None
    assert nudge_message({**payload, "stop_hook_active": True}, state, threshold=1) is None
    t.write_text(_line(*["Edit"] * 6), encoding="utf-8")
    assert nudge_message(payload, state, threshold=3) is not None


def test_nudge_silent_below_threshold(tmp):
    t = tmp / "t.jsonl"
    t.write_text(_line("Edit"), encoding="utf-8")
    assert nudge_message({"transcript_path": str(t)}, tmp / "s.json", threshold=8) is None


def test_nudge_cli_exits_2_with_stderr(tmp):
    (tmp / ".cairn").mkdir()
    (tmp / ".cairn" / "config.yaml").write_text("memory: {nudge: {edits: 2}}\n", encoding="utf-8")
    t = tmp / "t.jsonl"
    t.write_text(_line("Edit", "Edit"), encoding="utf-8")
    code = "import sys; sys.argv=['cairn','memory','nudge']; from cairn.interface.cli import main; main()"
    out = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps({"cwd": str(tmp), "transcript_path": str(t), "session_id": "x"}),
        capture_output=True, text=True, cwd=tmp,
    )
    assert out.returncode == 2 and "record_decision" in out.stderr


def test_propose_from_git_parses_and_filters():
    reply = 'Sure:\n[{"decision": "Use uv for env management", "commit": "abc1"}, {"decision": ""}, 3]'
    items = propose_from_git("commit abc1\nswitch to uv", lambda prompt, system: reply)
    assert [i["decision"] for i in items] == ["Use uv for env management"]
    assert propose_from_git("x", lambda p, s: None) is None
    assert propose_from_git("x", lambda p, s: "no json here") == []
