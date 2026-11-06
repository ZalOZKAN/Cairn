"""Learning from a session: a helper model reads only the part of the transcript that is new since the last run and
proposes decisions. A proposal is kept only when its evidence is a word-for-word quote from that text, and it goes to the
inbox, never straight into the decisions.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from cairn.memory import inbox
from cairn.memory.capture import assistant_text

MAX_CHARS = 60_000          # one helper call never reads more than this
MIN_CHARS = 1_500           # less than this is not enough to learn from
MAX_PROPOSALS = 5

LEARN_PROMPT = """You read part of a conversation between a developer and an AI coding assistant.
List the design or process DECISIONS that were settled in it and that a future session should respect.
Only settled decisions (both sides agreed or the user said so), not ideas, questions or trivial choices.
Reply ONLY with a JSON array, at most 5 items, empty [] when there is nothing:
[{"decision": "one sentence", "why": "the reason, one sentence", "rejected": ["an option that was ruled out"],
  "evidence": "a quote of 12 or more words copied EXACTLY from the conversation that shows the decision"}]
Conversation:
"""


def _user_text(entry: dict) -> str:
    if not isinstance(entry, dict) or entry.get("type") != "user":
        return ""
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    else:
        return ""
    return "" if text.lstrip().startswith("<") else text          # harness messages and command echoes


def new_text(transcript: Path, cursor_file: Path, session_id: str, advance: bool = True) -> str:
    """The conversation text added since the last call for this session (read from a saved byte offset)."""
    try:
        cursors = json.loads(cursor_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cursors = {}
    start = int(cursors.get(session_id, 0)) if isinstance(cursors, dict) else 0
    try:
        with transcript.open("rb") as f:
            f.seek(0, 2)
            end = f.tell()
            f.seek(start if start <= end else 0)
            raw = f.read().decode("utf-8", errors="ignore")
    except OSError:
        return ""
    parts = []
    for line in raw.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        user, bot = _user_text(entry), assistant_text(entry)
        if user.strip():
            parts.append("User: " + user.strip())
        elif bot.strip():
            parts.append("Assistant: " + bot.strip())
    if advance:
        cursors = cursors if isinstance(cursors, dict) else {}
        cursors[session_id] = end
        cursor_file.parent.mkdir(parents=True, exist_ok=True)
        cursor_file.write_text(json.dumps(cursors), encoding="utf-8")
    return "\n".join(parts)


def propose(text: str, helper: Callable[[str, str | None], str | None]) -> list[dict] | None:
    """Decision proposals with a verified quote. None means the helper model is not available."""
    if len(text) < MIN_CHARS:
        return []
    raw = helper(LEARN_PROMPT + text[-MAX_CHARS:], None)
    if raw is None:
        return None
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return [i for i in items if isinstance(i, dict) and isinstance(i.get("decision"), str) and i["decision"].strip()
            and isinstance(i.get("evidence"), str) and inbox.quote_in(i["evidence"], text)][:MAX_PROPOSALS]


def learn(cairn_dir: Path, transcript: Path, session_id: str, helper: Callable[[str, str | None], str | None], source: str = "stop") -> dict:
    """One pass: read what is new, ask the helper, put verified proposals in the inbox. Returns counts."""
    cursor = inbox.inbox_dir(cairn_dir) / ".cursor.json"
    text = new_text(transcript, cursor, session_id, advance=False)
    found = propose(text, helper)
    if found is None:
        return {"available": False, "added": 0, "repeats": 0}
    if len(text) >= MIN_CHARS:                                    # only a real pass moves the cursor
        new_text(transcript, cursor, session_id, advance=True)
    added = repeats = 0
    for p in found:
        got = inbox.add(cairn_dir, text=p["decision"], why=p.get("why") or "", evidence=p["evidence"], source=source,
                        rejected=[r for r in (p.get("rejected") or []) if isinstance(r, str)])
        if got["dismissed"]:
            continue
        added, repeats = added + (not got["duplicate"]), repeats + got["duplicate"]
    inbox.purge(cairn_dir)
    return {"available": True, "added": added, "repeats": repeats}
