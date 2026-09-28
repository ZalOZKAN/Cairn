from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from cairn.memory.capture import (
    decision_language_reminder,
    edits_since_last_decision,
    looks_like_decision,
    nudge_message,
    propose_from_git,
)


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


@pytest.mark.parametrize("text", [
    "Heads-up, no code changes today: the team decided to drop requests. From now on all "
    "outbound HTTP calls use httpx, because the services are moving to async.",
    "Add a 1.4.0 entry to CHANGELOG.md. FYI, unrelated: in today's meeting we agreed to drop "
    "requests and use httpx for all outbound HTTP from now on.",
    "We're switching to pnpm for this monorepo going forward.",
    "Let's use argon2id instead of bcrypt for password hashing.",
    "We'll use UUIDv7 for primary keys from now on.",
    "Stop using the legacy auth middleware; it's deprecated.",
])
def test_looks_like_decision_matches_decisions_stated_in_passing(text):
    assert looks_like_decision(text) is not None


@pytest.mark.parametrize("text", [
    "Add a price field to the Product dataclass in shop/models.py.",
    "Fix the bug where get_item crashes when the item doesn't exist.",
    "Add a 1.4.0 entry to CHANGELOG.md with one line about the rounding fix.",
    "Refactor the pricing module to use a helper function.",
    "Please review this PR when you get a chance.",
    "Add a migration that drops the legacy_status column.",
    "Drop the temp table after the migration runs.",
    "Add a DROP INDEX statement for the old index.",
    "The response returns the discounted price instead of the original.",
])
def test_looks_like_decision_is_silent_on_ordinary_requests(text):
    assert looks_like_decision(text) is None


def test_decision_language_reminder_quotes_the_matched_phrase():
    msg = decision_language_reminder({"prompt": "From now on we use httpx, not requests."})
    assert msg is not None and "from now on" in msg.lower() and "record_decision" in msg


def test_decision_language_reminder_silent_without_a_prompt_or_match():
    assert decision_language_reminder({}) is None
    assert decision_language_reminder({"prompt": "Add a price field."}) is None


def test_detect_hook_cli_prints_additional_context_only_when_it_matches(tmp):
    (tmp / ".cairn").mkdir()
    code = "import sys; sys.argv=['cairn','memory','detect']; from cairn.interface.cli import main; main()"

    def fire(prompt):
        out = subprocess.run([sys.executable, "-c", code],
                             input=json.dumps({"cwd": str(tmp), "prompt": prompt}),
                             capture_output=True, text=True, cwd=tmp)
        return out.stdout.strip()

    hit = fire("We agreed to drop requests and switch to httpx from now on.")
    data = json.loads(hit)
    assert data["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "record_decision" in data["hookSpecificOutput"]["additionalContext"]
    assert fire("Add a price field to the Product dataclass.") == ""


def test_detect_hook_echoes_gemini_clis_own_event_name(tmp):
    """Gemini CLI's BeforeAgent input (captured from a real 0.61.0 run) has
    the same `prompt`/`cwd` fields plus `hook_event_name: "BeforeAgent"`; the
    output must carry that name back, and stay silent (no plain text: Gemini
    wants JSON-only stdout) when nothing matches."""
    (tmp / ".cairn").mkdir()
    code = "import sys; sys.argv=['cairn','memory','detect']; from cairn.interface.cli import main; main()"
    base = {"session_id": "s", "transcript_path": "t.jsonl", "cwd": str(tmp),
            "hook_event_name": "BeforeAgent", "timestamp": "2026-09-28T14:15:42.221Z"}

    def fire(prompt):
        out = subprocess.run([sys.executable, "-c", code], input=json.dumps({**base, "prompt": prompt}),
                             capture_output=True, text=True, cwd=tmp)
        return out.stdout.strip()

    data = json.loads(fire("from now on we use httpx for all outbound HTTP"))
    assert data["hookSpecificOutput"]["hookEventName"] == "BeforeAgent"
    assert "record_decision" in data["hookSpecificOutput"]["additionalContext"]
    assert fire("Add a price field to the Product dataclass.") == ""


def _gemini_record(*names: str) -> str:
    return json.dumps({"id": "x", "type": "gemini", "content": "",
                       "toolCalls": [{"name": n, "args": {}} for n in names]})


def test_edit_count_understands_a_gemini_cli_transcript(tmp):
    """Shape taken from a real Gemini CLI 0.61.0 session file: header, user
    records, `$set` bookkeeping lines, and model records carrying `toolCalls`
    (write_file / replace are its edit tools; MCP tools are mcp_<server>_<tool>)."""
    t = tmp / "session.jsonl"
    t.write_text("\n".join([
        json.dumps({"sessionId": "a", "kind": "main"}),
        json.dumps({"id": "u", "type": "user", "content": [{"text": "do it"}]}),
        json.dumps({"$set": {"lastUpdated": "now"}}),
        _gemini_record("write_file", "replace", "read_file"),
        _gemini_record("mcp_cairn_record_decision"),
        _gemini_record("write_file", "run_shell_command"),
    ]), encoding="utf-8")
    assert edits_since_last_decision(t) == 1


def test_propose_from_git_parses_and_filters():
    reply = 'Sure:\n[{"decision": "Use uv for env management", "commit": "abc1"}, {"decision": ""}, 3]'
    items = propose_from_git("commit abc1\nswitch to uv", lambda prompt, system: reply)
    assert [i["decision"] for i in items] == ["Use uv for env management"]
    assert propose_from_git("x", lambda p, s: None) is None
    assert propose_from_git("x", lambda p, s: "no json here") == []
