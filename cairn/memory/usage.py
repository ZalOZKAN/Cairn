"""Measure Cairn in real sessions, from the host's own transcripts.

Two questions a user should be able to answer without trusting a benchmark:

1. Did Cairn miss decisions? Every user message that reads like a decision
   ("from now on...", "bundan sonra...", "我们决定...") is a signal. A signal
   is covered when a `record_decision` call follows it before the next user
   message; otherwise it is a possible miss, listed with its quote by
   `cairn memory missed` so a human can judge it.
2. Does it save tokens? Claude Code writes each model call's token usage into
   its transcript. Sessions that used Cairn tools are compared with sessions
   that did not, by `cairn memory usage`.

Read-only, local: transcripts are read where the host already keeps them and
nothing is sent anywhere. Only Claude Code transcripts are read for now.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from cairn.memory.capture import _tool_uses, assistant_text
from cairn.memory.lang import DECISION_SIGNALS, PROPOSAL_RE, is_affirmation

QUOTE_CHARS = 160


_PASTED = re.compile(r"<pasted_content[^>]*>.*?</pasted_content[^>]*>", re.DOTALL)
_TAGGED = re.compile(r"<(system-reminder|command-[a-z-]+|local-command-[a-z-]+)>.*?</\1>", re.DOTALL)
_CONTINUATION = "This session is being continued from a previous conversation"


def clean_user_text(text: str) -> str:
    """What the person wrote themselves: pasted documents, logs and
    harness-added blocks are removed, since a decision phrase inside a pasted
    log is not a decision the user just made."""
    return _TAGGED.sub(" ", _PASTED.sub(" ", text))


def _user_text(entry: dict) -> str | None:
    """The words a person typed, or None for tool results and other records."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary"):
        return None
    msg = entry.get("message") if isinstance(entry.get("message"), dict) else entry
    content = msg.get("content")
    if isinstance(content, str):
        # Claude Code also logs slash-command output and caveats as user
        # records; they start with its own tags, never with what a person typed.
        if content.lstrip().startswith(("<command-", "<local-command", "Caveat:", _CONTINUATION)):
            return None
        return content
    if isinstance(content, list):
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(texts) if texts else None
    return None


def _usage(entry: dict) -> tuple[str | None, dict]:
    msg = entry.get("message")
    if entry.get("type") != "assistant" or not isinstance(msg, dict):
        return None, {}
    return msg.get("id"), msg.get("usage") or {}


def session_report(transcript: Path) -> dict[str, Any]:
    """Signals, coverage, Cairn tool calls and token usage for one session."""
    try:
        lines = transcript.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        lines = []
    signals: list[dict] = []
    pending: dict | None = None
    last_assistant = ""
    cairn_calls = recorded = 0
    seen_msgs: set[str] = set()
    tokens = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
    started = ended = None
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        ts = entry.get("timestamp")
        if isinstance(ts, str):
            started = started or ts
            ended = ts
        text = _user_text(entry)
        if text is not None and text.strip():
            if pending is not None:
                signals.append(pending)
            own = " ".join(clean_user_text(text).split())
            match = DECISION_SIGNALS.search(own) if not own.startswith(_CONTINUATION) else None
            pending = ({"signal": match.group(0), "quote": _around(own, match.start()), "at": ts,
                        "covered": False} if match else None)
            if pending is None and is_affirmation(own) and PROPOSAL_RE.search(last_assistant[-600:]):
                # a "yes" to a proposal: the decision is in the assistant's words
                pending = {"signal": "yes: " + own[:30], "quote": "..." + " ".join(last_assistant[-160:].split()),
                           "at": ts, "covered": False}
            continue
        said = assistant_text(entry).strip()
        if said:
            last_assistant = said
        for name in _tool_uses(entry):
            if "cairn" in name or name in ("record_decision", "memory_context", "memory_boot"):
                cairn_calls += 1
            if name.endswith("record_decision"):
                recorded += 1
                if pending is not None:
                    pending["covered"] = True
        msg_id, usage = _usage(entry)
        if usage and msg_id not in seen_msgs:
            if msg_id:
                seen_msgs.add(msg_id)
            tokens["input"] += int(usage.get("input_tokens") or 0)
            tokens["cache_write"] += int(usage.get("cache_creation_input_tokens") or 0)
            tokens["cache_read"] += int(usage.get("cache_read_input_tokens") or 0)
            tokens["output"] += int(usage.get("output_tokens") or 0)
    if pending is not None:
        signals.append(pending)
    return {
        "session": transcript.stem, "started": started, "ended": ended,
        "model_calls": len(seen_msgs), "cairn_calls": cairn_calls, "recorded": recorded,
        "signals": signals, "missed": [s for s in signals if not s["covered"]], "tokens": tokens,
    }


def _around(text: str, pos: int) -> str:
    """A quote centred on the matched phrase, so the reader sees the decision."""
    start = max(0, pos - QUOTE_CHARS // 3)
    snippet = text[start:start + QUOTE_CHARS]
    return ("..." if start else "") + snippet + ("..." if start + QUOTE_CHARS < len(text) else "")


def claude_transcripts(project_dir: Path, claude_home: Path | None = None) -> list[Path]:
    """Claude Code's transcripts for this project, oldest first."""
    from cairn.memory.bridge import claude_memory_dir
    folder = claude_memory_dir(project_dir, claude_home=claude_home).parent
    if not folder.is_dir():
        return []
    return sorted(folder.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)


def project_report(project_dir: Path, claude_home: Path | None = None, days: int | None = None) -> dict[str, Any]:
    """Every session, plus totals split by whether Cairn was used."""
    sessions = [session_report(p) for p in claude_transcripts(project_dir, claude_home)]
    sessions = [s for s in sessions if s["model_calls"] or s["signals"]]
    if days is not None:
        cutoff = datetime.now().timestamp() - days * 86400
        sessions = [s for s in sessions if s["ended"] and _ts(s["ended"]) >= cutoff]
    groups: dict[str, dict[str, Any]] = {}
    for key, rows in (("with_cairn", [s for s in sessions if s["cairn_calls"]]),
                      ("without_cairn", [s for s in sessions if not s["cairn_calls"]])):
        n = len(rows)
        total = {k: sum(s["tokens"][k] for s in rows) for k in ("input", "cache_write", "cache_read", "output")}
        groups[key] = {
            "sessions": n,
            "avg_tokens": {k: round(v / n) for k, v in total.items()} if n else {},
            "avg_model_calls": round(sum(s["model_calls"] for s in rows) / n, 1) if n else 0,
        }
    signals = sum(len(s["signals"]) for s in sessions)
    missed = sum(len(s["missed"]) for s in sessions)
    return {
        "sessions": sessions, "groups": groups,
        "signals": signals, "missed": missed,
        "capture_rate": round((signals - missed) / signals, 3) if signals else None,
    }


def _ts(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def billable_input(tokens: dict) -> float:
    """Input tokens weighted the way Anthropic bills them: cache writes at
    1.25x, cache reads at 0.1x. A rough cost proxy that does not depend on
    the model's price."""
    return tokens.get("input", 0) + 1.25 * tokens.get("cache_write", 0) + 0.1 * tokens.get("cache_read", 0)
