"""Host probe: what does an AI tool's hook actually send?

Run as a hook (`cairn memory probe --host NAME`), it appends one line of
structural facts to `.cairn/persistence/host-probe.jsonl`: which event fired,
which keys the payload had and their types, whether a transcript path was
given and readable, which record types its last lines hold, and whether the
assistant's text could be extracted. It never stores message text, paths or
values, and prints nothing, so it cannot inject anything into a session.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from cairn.memory.capture import last_assistant_text

PROBE_FILE = "host-probe.jsonl"
_TAIL_BYTES = 200_000
_LAST_RECORDS = 20


def _transcript_facts(raw_path: str) -> dict:
    facts = {"transcript_exists": False, "transcript_readable": False, "transcript_format": None,
             "record_types": {}, "assistant_extracted": False, "assistant_chars": 0}
    try:
        path = Path(raw_path)
        facts["transcript_exists"] = path.is_file()
        if not facts["transcript_exists"]:
            return facts
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - _TAIL_BYTES))
            tail = f.read().decode("utf-8", errors="ignore")
        facts["transcript_readable"] = True
    except OSError:
        return facts
    lines = [line for line in tail.splitlines() if line.strip()]
    records = []
    for line in lines[-_LAST_RECORDS:]:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            records.append(value)
    if records:
        facts["transcript_format"] = "jsonl"
        kinds = Counter(str(r.get("type") or r.get("role") or "?") for r in records)
        facts["record_types"] = dict(kinds)
    else:
        facts["transcript_format"] = "json" if tail.lstrip()[:1] in "{[" else "other"
    text = last_assistant_text(path)
    facts["assistant_extracted"] = bool(text)
    facts["assistant_chars"] = len(text)
    return facts


def structure(payload: dict, host: str) -> dict:
    """The line to store: facts about the payload, never its values."""
    path_key = next((k for k in ("transcript_path", "transcriptPath") if payload.get(k)), None)
    line = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": host,
        "event": str(payload.get("hook_event_name") or payload.get("event") or payload.get("type") or "") or None,
        "keys": {k: type(v).__name__ for k, v in payload.items()},
        "transcript_key": path_key,
        "prompt_present": bool(payload.get("prompt")),
        "prompt_chars": len(payload["prompt"]) if isinstance(payload.get("prompt"), str) else 0,
    }
    line.update(_transcript_facts(str(payload[path_key])) if path_key else
                {"transcript_exists": False, "transcript_readable": False, "transcript_format": None,
                 "record_types": {}, "assistant_extracted": False, "assistant_chars": 0})
    return line


def record(project_dir: Path, payload: dict, host: str) -> None:
    cairn_dir = project_dir / ".cairn"
    if not cairn_dir.is_dir():
        return
    out = cairn_dir / "persistence" / PROBE_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(structure(payload, host), ensure_ascii=False) + "\n")


def report(project_dir: Path) -> list[dict]:
    """One summary row per host."""
    path = project_dir / ".cairn" / "persistence" / PROBE_FILE
    if not path.is_file():
        return []
    rows: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        row = rows.setdefault(item["host"], {"host": item["host"], "probes": 0, "events": set(), "keys": set(),
                                             "readable": 0, "extracted": 0, "prompts": 0, "types": Counter()})
        row["probes"] += 1
        row["events"].add(item.get("event") or "?")
        row["keys"].update(item.get("keys") or {})
        row["readable"] += bool(item.get("transcript_readable"))
        row["extracted"] += bool(item.get("assistant_extracted"))
        row["prompts"] += bool(item.get("prompt_present"))
        row["types"].update(item.get("record_types") or {})
    return [{
        "host": r["host"], "probes": r["probes"], "events": sorted(r["events"]), "keys": sorted(r["keys"]),
        "prompt_rate": round(r["prompts"] / r["probes"], 2),
        "transcript_rate": round(r["readable"] / r["probes"], 2),
        "extraction_rate": round(r["extracted"] / r["probes"], 2),
        "record_types": dict(r["types"]),
    } for r in rows.values()]
