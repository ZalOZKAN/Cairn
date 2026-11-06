"""The inbox, the correction catcher and lessons: proposals wait for a person, repeats are counted, nothing leaks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cairn.memory import inbox, lessons
from cairn.memory.corrections import detect_correction


class FakeMemory:
    def __init__(self):
        self.calls = []

    def record_decision(self, decision, **kw):
        self.calls.append((decision, kw))
        return 7


def test_a_proposal_waits_and_a_repeat_raises_its_count(tmp_path):
    first = inbox.add(tmp_path, text="Money is stored as integer cents", why="float rounding", evidence="we agreed on integer cents")
    assert first["duplicate"] is False and first["seen"] == 1
    again = inbox.add(tmp_path, text="Money is stored as integer cents")
    assert again["id"] == first["id"] and again["seen"] == 2 and again["ready"] is False
    third = inbox.add(tmp_path, text="money is stored as integer cents")
    assert third["seen"] == 3 and third["ready"] is True
    assert len(inbox.items(tmp_path)) == 1
    assert inbox.summary(tmp_path) == {"pending": 1, "ready": 1}


def test_secrets_are_removed_and_fields_are_capped(tmp_path):
    key = "sk-" + "a1B2" * 8
    got = inbox.add(tmp_path, text=f"Use key {key} for the call", evidence="x" * 9000)
    item = inbox.get(tmp_path, got["id"])
    assert key not in item["text"] and len(item["evidence"]) <= inbox.MAX_FIELD


def test_confirm_records_a_decision_and_empties_the_inbox(tmp_path):
    got = inbox.add(tmp_path, text="Use httpOnly cookies for the JWT", why="XSS", evidence="we chose httpOnly cookies",
                    rejected=["localStorage"], scope_path="web", source="stop")
    memory = FakeMemory()
    assert inbox.confirm(tmp_path, got["id"], memory) == 7
    text, kw = memory.calls[0]
    assert text == "Use httpOnly cookies for the JWT" and kw["reasoning"] == "XSS" and kw["scope_path"] == "web"
    assert kw["alternatives"][0]["option"] == "localStorage" and kw["evidence"][0]["claim"] == "we chose httpOnly cookies"
    assert inbox.items(tmp_path) == []
    with pytest.raises(KeyError):
        inbox.confirm(tmp_path, got["id"], memory)


def test_a_dismissed_proposal_is_not_proposed_again(tmp_path):
    got = inbox.add(tmp_path, text="Switch the build to Bazel")
    assert inbox.dismiss(tmp_path, got["id"]) is True and inbox.dismiss(tmp_path, got["id"]) is False
    again = inbox.add(tmp_path, text="Switch the build to Bazel")
    assert again["dismissed"] is True and inbox.items(tmp_path) == []


def test_old_proposals_are_purged_and_bad_input_is_refused(tmp_path):
    got = inbox.add(tmp_path, text="Keep the cache in memory")
    later = datetime.now(timezone.utc) + timedelta(days=inbox.KEEP_DAYS + 1)
    assert inbox.purge(tmp_path, now=later) == 1 and inbox.items(tmp_path) == []
    for kw in ({"text": " "}, {"text": "x", "source": "robot"}):
        with pytest.raises(ValueError):
            inbox.add(tmp_path, **kw)
    assert inbox.get(tmp_path, "../x") is None and inbox.get(tmp_path, got["id"]) is None


def test_a_quote_must_really_occur_in_the_text():
    text = "User: should we use httpOnly cookies?\nAssistant: yes, we decided to store the JWT in httpOnly cookies."
    assert inbox.quote_in("we decided to store the JWT in httpOnly cookies", text)
    assert inbox.quote_in("WE DECIDED  to store the JWT in httpOnly cookies", text)
    assert not inbox.quote_in("we decided to use localStorage for tokens", text)
    assert not inbox.quote_in("yes", text)                                   # too short to prove anything


@pytest.mark.parametrize("prompt,text,rejected", [
    ("no, use pytest instead of unittest", "Use pytest instead of unittest", ["unittest"]),
    ("Use sqlite instead of postgres for this", "Use sqlite instead of postgres for this", ["postgres for this"]),
    ("don't use moment.js", "Do not use moment.js", ["moment.js"]),
    ("hayır, pytest kullan", "Use pytest", []),
    ("unittest yerine pytest kullan", "Use pytest instead of unittest", ["unittest"]),
    ("lodash kullanma", "Do not use lodash", ["lodash"]),
])
def test_corrections_are_found(prompt, text, rejected):
    got = detect_correction(prompt)
    assert got and got["text"].startswith(text.split(" for this")[0]) and got["evidence"] == prompt
    if rejected:
        assert got["rejected"][0].startswith(rejected[0].split(" for this")[0])


@pytest.mark.parametrize("prompt", ["please add a login page", "I use vim every day", "", "<system-reminder>no, use x</system-reminder>",
                                    "x " * 400 + "no, use y"])
def test_ordinary_messages_are_not_corrections(prompt):
    assert detect_correction(prompt) is None


def test_lessons_are_shown_for_their_files_and_fade_unless_stated(tmp_path):
    a = lessons.record(tmp_path, "Reset the test database before this suite", ["tests/db/test_orders.py"], origin="inferred", confidence=2)
    b = lessons.record(tmp_path, "Never call the live API in tests", ["tests/"], origin="user-stated", confidence=3)
    assert [x["id"] for x in lessons.for_path(tmp_path, "tests/db/test_orders.py")] == [b["id"], a["id"]]
    assert lessons.for_path(tmp_path, "src/app.py") == []
    old = datetime.now(timezone.utc) + timedelta(days=lessons.FADE_DAYS * 3)
    kept = [x["id"] for x in lessons.for_path(tmp_path, "tests/db/test_orders.py", now=old)]
    assert kept == [b["id"]]                                               # the inferred lesson faded to 0, the stated one stays


def test_a_repeated_lesson_is_counted_and_stale_ones_are_found(tmp_path):
    first = lessons.record(tmp_path, "The parser needs a trailing newline", ["src/parser.py"])
    again = lessons.record(tmp_path, "the parser needs a trailing newline", ["src/lexer.py"])
    assert again["id"] == first["id"] and again["seen"] == 2 and again["files"] == ["src/lexer.py", "src/parser.py"]
    (tmp_path / "project").mkdir()
    assert [x["id"] for x in lessons.stale(tmp_path, tmp_path / "project")] == [first["id"]]
    assert lessons.forget(tmp_path, first["id"]) is True and lessons.forget(tmp_path, first["id"]) is False
    with pytest.raises(ValueError):
        lessons.record(tmp_path, "no files", [])
