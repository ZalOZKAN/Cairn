"""Capture helpers: decision-language detection, the Stop-hook nudge, and
git-history decision proposals.

None of these write a decision on their own. The detector and the nudge only
remind the agent; the git capture only proposes, and a human confirms each
record.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

# Claude Code's edit tools, then Gemini CLI's (write_file, replace).
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "write_file", "replace"}

# A decision is often stated in passing, not as an explicit "please record
# this": "the team decided...", "from now on...", "bundan sonra...". Pure
# regex, no LLM, so it costs nothing when it doesn't match; a false positive
# costs one line the agent is told it may ignore, so recall matters more than
# precision here. The per-language phrase lists live in cairn/memory/lang.py.
# Bare "drop X" and "instead of" are left out on purpose: both are extremely
# common in ordinary code (SQL DROP TABLE, "return X instead of Y").
from cairn.memory.lang import DECISION_SIGNALS, PROPOSAL_RE, is_affirmation  # noqa: E402


def looks_like_decision(text: str) -> str | None:
    """The matched signal phrase (for use as evidence), or None."""
    m = DECISION_SIGNALS.search(text or "")
    return m.group(0) if m else None


def assistant_text(entry: dict) -> str:
    """The words the assistant wrote in one transcript record. Claude Code
    writes `type: assistant` with text blocks; Gemini CLI's model records
    carry a `content` string. Tool calls and thinking are not text."""
    if not isinstance(entry, dict) or entry.get("type") not in ("assistant", "gemini", "model"):
        return ""
    msg = entry.get("message") if isinstance(entry.get("message"), dict) else entry
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def last_assistant_text(transcript: Path, tail_bytes: int = 200_000) -> str:
    """The assistant's most recent message, read from the end of the
    transcript so a long session costs one small read."""
    try:
        with transcript.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - tail_bytes))
            lines = f.read().decode("utf-8", errors="ignore").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            text = assistant_text(json.loads(line)).strip()
        except json.JSONDecodeError:
            continue
        if text:
            return text
    return ""


def accepted_proposal(prompt: str, transcript: Path | None) -> str | None:
    """When the user's message is a short yes and the assistant's last message
    was a proposal or a question, that message's closing lines (the part that
    holds the proposal); else None. This is how "tamam, Redis'le gidelim" is
    caught: the decision is in the assistant's words, the user only agrees."""
    if transcript is None or not is_affirmation(prompt):
        return None
    last = last_assistant_text(transcript)
    if not last or not PROPOSAL_RE.search(last[-600:]):
        return None
    return " ".join(last[-400:].split())


def decision_language_reminder(payload: dict) -> str | None:
    """Reminder text for a UserPromptSubmit (Claude Code) or BeforeAgent
    (Gemini CLI) hook, or None to stay silent. Both send the user's own message
    as `payload["prompt"]`; matched text is quoted back so the agent (and
    `record_decision`'s evidence) can point at exactly what triggered this,
    rather than a generic warning. A short "yes" is read together with the
    assistant's last message in `payload["transcript_path"]`."""
    prompt = payload.get("prompt") or ""
    signal = looks_like_decision(prompt)
    if not signal:
        path = payload.get("transcript_path")
        proposal = accepted_proposal(prompt, Path(path) if path else None)
        if not proposal:
            return None
        return (
            f'Cairn: the user answered "{prompt.strip()[:40]}" to your last message ("...{proposal}"). If that '
            "settled a design choice (library, pattern, data model, API shape, trade-off), call record_decision "
            "(reasoning, rejected alternatives, scope_path) before moving on. If it was only a go-ahead for a "
            "task or settled nothing, ignore this."
        )
    return (
        f'Cairn: this message contains decision-like language ("{signal}"). If it settles '
        "a design choice (library, pattern, data model, API shape, trade-off) even in "
        "passing, call record_decision (reasoning, rejected alternatives, scope_path) "
        "before moving on, quoting the user's own words as evidence. If it is not "
        "actually a decision, ignore this."
    )


def _tool_uses(node: Any):
    """Tool names in a transcript record. Claude Code writes `tool_use` blocks;
    Gemini CLI writes a `toolCalls` list of `{name, args, ...}` on each model
    record (checked against real 0.61.0 session files)."""
    if isinstance(node, dict):
        if node.get("type") == "tool_use" and isinstance(node.get("name"), str):
            yield node["name"]
        for call in node.get("toolCalls") or []:
            if isinstance(call, dict) and isinstance(call.get("name"), str):
                yield call["name"]
        for v in node.values():
            yield from _tool_uses(v)
    elif isinstance(node, list):
        for v in node:
            yield from _tool_uses(v)


def edits_since_last_decision(transcript: Path) -> int:
    """Count file-edit tool calls after the most recent record_decision call
    in a Claude Code or Gemini CLI transcript (JSONL). Unparseable lines are
    ignored."""
    edits = 0
    try:
        lines = transcript.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return 0
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        for name in _tool_uses(entry):
            if name.endswith("record_decision"):
                edits = 0
            elif name in EDIT_TOOLS:
                edits += 1
    return edits


def nudge_message(payload: dict, state_file: Path, threshold: int) -> str | None:
    """Reminder text for a Stop hook, or None to stay silent. Registered for
    Claude Code only; the transcript reader also understands Gemini CLI's, for
    when an AfterAgent equivalent is verified.

    Silent when: the hook is already continuing from a previous nudge
    (`stop_hook_active`), fewer than `threshold` edits happened since the
    last recorded decision, or we already nudged at this edit count."""
    if payload.get("stop_hook_active"):
        return None
    transcript = payload.get("transcript_path")
    if not transcript:
        return None
    edits = edits_since_last_decision(Path(transcript))
    if edits < threshold:
        return None
    session = str(payload.get("session_id") or transcript)
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        state = {}
    last = state.get(session, 0)
    if last and edits < last + threshold:
        return None
    state[session] = edits
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state), encoding="utf-8")
    return (
        f"Cairn: {edits} file edits since the last recorded decision. If this work "
        "settled a design choice (library, pattern, data model, API shape, trade-off), "
        "record it now with record_decision (reasoning, rejected alternatives, "
        "scope_path). If nothing was decided, ignore this and finish."
    )


CAPTURE_PROMPT = """You read git history and extract DESIGN DECISIONS: a choice between
options that a future developer should not silently undo (library/tool choice,
architecture, data model, API shape, conventions, trade-offs). Ignore routine
fixes, refactors, version bumps and formatting.

Return ONLY a JSON array (possibly empty). Each item:
{"decision": "<one sentence>", "reasoning": "<why, from the commit text>",
 "alternatives": [{"option": "...", "rejected_because": "..."}],
 "scope_path": "<directory it applies to, or null>", "commit": "<short sha>"}
Never invent reasoning that the commits do not support; leave it empty instead.

Commits:
"""


def propose_from_git(
    log_text: str, helper: Callable[[str, str | None], str | None]
) -> list[dict] | None:
    """Ask the helper model for decision proposals. None = helper unavailable."""
    raw = helper(CAPTURE_PROMPT + log_text[:12000], None)
    if raw is None:
        return None
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return [
        i for i in items
        if isinstance(i, dict) and isinstance(i.get("decision"), str) and i["decision"].strip()
    ]
