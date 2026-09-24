"""Capture helpers: the Stop-hook nudge and git-history decision proposals.

Neither writes a decision on its own. The nudge only reminds the agent; the
git capture only proposes, and a human confirms each record.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _tool_uses(node: Any):
    if isinstance(node, dict):
        if node.get("type") == "tool_use" and isinstance(node.get("name"), str):
            yield node["name"]
        for v in node.values():
            yield from _tool_uses(v)
    elif isinstance(node, list):
        for v in node:
            yield from _tool_uses(v)


def edits_since_last_decision(transcript: Path) -> int:
    """Count file-edit tool calls after the most recent record_decision call
    in a Claude Code transcript (JSONL). Unparseable lines are ignored."""
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
    """Reminder text for a Stop hook, or None to stay silent.

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
