"""Learning from a transcript: only new text is read, only quotes that really occur are kept, everything lands in the inbox."""
from __future__ import annotations

import json

from cairn.memory import inbox
from cairn.memory.learn import MIN_CHARS, learn, new_text, propose

QUOTE = "we decided to keep every price as integer cents and never use floating point numbers anywhere"


def line(kind: str, text: str) -> str:
    if kind == "user":
        return json.dumps({"type": "user", "message": {"role": "user", "content": text}})
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}})


def make_transcript(path, *texts):
    path.write_text("\n".join(line(k, t) for k, t in texts) + "\n", encoding="utf-8")


def filler():
    return "x" * MIN_CHARS


def helper_returning(items):
    calls = []

    def helper(prompt, model=None):
        calls.append(prompt)
        return json.dumps(items)
    helper.calls = calls
    return helper


def test_only_new_text_is_read_and_harness_messages_are_skipped(tmp_path):
    t, cursor = tmp_path / "t.jsonl", tmp_path / "cursor.json"
    make_transcript(t, ("user", "first question"), ("assistant", "first answer"), ("user", "<system-reminder>skip me</system-reminder>"))
    first = new_text(t, cursor, "s1")
    assert "User: first question" in first and "Assistant: first answer" in first and "skip me" not in first
    assert new_text(t, cursor, "s1") == ""
    with t.open("a", encoding="utf-8") as f:
        f.write(line("user", "second question") + "\n")
    assert new_text(t, cursor, "s1") == "User: second question"
    assert new_text(t, cursor, "other-session") != ""                  # each session has its own place


def test_a_proposal_needs_a_real_quote(tmp_path):
    text = f"User: how do we store money?\nAssistant: {QUOTE}.\n" + filler()
    good = {"decision": "Prices are integer cents", "why": "float rounding", "evidence": QUOTE, "rejected": ["floating point"]}
    invented = {"decision": "Use Redis", "why": "speed", "evidence": "the team agreed that redis is the only cache we will ever use"}
    got = propose(text, helper_returning([good, invented, {"nonsense": 1}]))
    assert [p["decision"] for p in got] == ["Prices are integer cents"]


def test_too_little_text_and_a_missing_helper(tmp_path):
    helper = helper_returning([])
    assert propose("short", helper) == [] and helper.calls == []
    assert propose(filler(), lambda prompt, model=None: None) is None


def test_learn_fills_the_inbox_counts_repeats_and_moves_the_cursor(tmp_path):
    cairn = tmp_path / ".cairn"
    t = tmp_path / "t.jsonl"
    make_transcript(t, ("user", "how do we store money?"), ("assistant", QUOTE + ". " + filler()))
    item = {"decision": "Prices are integer cents", "why": "float rounding", "evidence": QUOTE, "rejected": ["floating point"]}
    helper = helper_returning([item])
    first = learn(cairn, t, "s1", helper)
    assert first == {"available": True, "added": 1, "repeats": 0}
    assert inbox.items(cairn)[0]["evidence"] == QUOTE and inbox.items(cairn)[0]["source"] == "stop"
    assert learn(cairn, t, "s1", helper) == {"available": True, "added": 0, "repeats": 0} and len(helper.calls) == 1   # nothing new: no call
    with t.open("a", encoding="utf-8") as f:
        f.write(line("assistant", QUOTE + ". " + filler()) + "\n")
    again = learn(cairn, t, "s1", helper)
    assert again["repeats"] == 1 and inbox.items(cairn)[0]["seen"] == 2


def test_an_unavailable_helper_leaves_the_cursor_alone(tmp_path):
    cairn, t = tmp_path / ".cairn", tmp_path / "t.jsonl"
    make_transcript(t, ("assistant", filler()))
    assert learn(cairn, t, "s1", lambda prompt, model=None: None)["available"] is False
    assert new_text(t, inbox.inbox_dir(cairn) / ".cursor.json", "s1", advance=False) != ""
