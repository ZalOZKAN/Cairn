"""The inbox: decisions that Cairn or an agent proposes on its own wait here until someone confirms them.

Nothing in the inbox reaches an agent or `AGENTS.md`. A proposal carries a word-for-word quote as evidence, is checked
against what is already waiting (a repeat raises its `seen` count instead of adding a copy), and is deleted after 30
days. A dismissed proposal is remembered so the same idea is not proposed again.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cairn.memory.dedup import candidate_relation
from cairn.security.guard import mask_secrets

KEEP_DAYS = 30                  # a pending proposal nobody confirmed is dropped after this
KEEP_DISMISSED_DAYS = 90        # a dismissed one is remembered a little longer, so it is not proposed again
READY_AT = 3                    # seen this many times: worth confirming
MAX_TEXT = 500
MAX_FIELD = 5000
SOURCES = ("agent", "stop", "compact", "correction", "git", "review", "user")


def inbox_dir(cairn_dir: Path) -> Path:
    return Path(cairn_dir) / "inbox"


def _clean(value: Any, limit: int) -> str:
    return mask_secrets(" ".join(str(value or "").split()))[:limit]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write(path: Path, item: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(item, ensure_ascii=False, indent=1), encoding="utf-8")


def _read_all(cairn_dir: Path) -> list[dict]:
    out = []
    for f in sorted(inbox_dir(cairn_dir).glob("*.json")):
        try:
            item = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(item, dict) and item.get("id") == f.stem:
            out.append(item)
    return out


def items(cairn_dir: Path) -> list[dict]:
    """The pending proposals, oldest first."""
    return [i for i in _read_all(cairn_dir) if i.get("status") == "pending"]


def get(cairn_dir: Path, item_id: str) -> dict | None:
    if not re.fullmatch(r"[0-9]{8}-[0-9a-f]{6}", item_id or ""):
        return None
    return next((i for i in _read_all(cairn_dir) if i["id"] == item_id), None)


def _same(a: str, b: str) -> bool:
    return a.casefold() == b.casefold() or candidate_relation(a, b)[0] == "possible-duplicate"


def add(cairn_dir: Path, *, text: str, why: str = "", evidence: str = "", source: str = "agent", rejected: list[str] | None = None,
        scope_path: str | None = None) -> dict:
    """Put a proposal in the inbox. Returns {"id", "seen", "ready", "duplicate", "dismissed"}; a dismissed or repeated
    proposal does not add a second file."""
    text = _clean(text, MAX_TEXT)
    if not text:
        raise ValueError("a proposal needs a decision text")
    if source not in SOURCES:
        raise ValueError(f"source is one of: {', '.join(SOURCES)}")
    now = _now().isoformat()
    for item in _read_all(cairn_dir):
        if not _same(text, item.get("text", "")):
            continue
        if item["status"] == "dismissed":
            return {"id": item["id"], "seen": item.get("seen", 1), "ready": False, "duplicate": True, "dismissed": True}
        item["seen"] = item.get("seen", 1) + 1
        item["last_seen"] = now
        _write(inbox_dir(cairn_dir) / f"{item['id']}.json", item)
        return {"id": item["id"], "seen": item["seen"], "ready": item["seen"] >= READY_AT, "duplicate": True, "dismissed": False}
    item_id = f"{_now().strftime('%Y%m%d')}-{secrets.token_hex(3)}"
    item = {
        "id": item_id, "status": "pending", "created": now, "last_seen": now, "seen": 1, "source": source,
        "text": text, "why": _clean(why, MAX_FIELD), "evidence": _clean(evidence, MAX_FIELD),
        "rejected": [_clean(r, 200) for r in (rejected or []) if _clean(r, 200)][:5],
        "scope_path": _clean(scope_path, 300) or None,
    }
    _write(inbox_dir(cairn_dir) / f"{item_id}.json", item)
    return {"id": item_id, "seen": 1, "ready": False, "duplicate": False, "dismissed": False}


def dismiss(cairn_dir: Path, item_id: str) -> bool:
    item = get(cairn_dir, item_id)
    if not item or item["status"] != "pending":
        return False
    item["status"] = "dismissed"
    item["dismissed"] = _now().isoformat()
    _write(inbox_dir(cairn_dir) / f"{item_id}.json", item)
    return True


def confirm(cairn_dir: Path, item_id: str, memory, recorded_by: str = "inbox") -> int | str:
    """Record a pending proposal as a real decision and remove it from the inbox. Returns the decision id."""
    item = get(cairn_dir, item_id)
    if not item or item["status"] != "pending":
        raise KeyError(item_id)
    alternatives = [{"option": r, "rejected_because": "rejected when the proposal was made"} for r in item.get("rejected", [])]
    evidence = [{"source": f"inbox ({item.get('source', 'agent')})", "claim": item["evidence"]}] if item.get("evidence") else None
    decision_id = memory.record_decision(
        item["text"], reasoning=item.get("why") or None, alternatives=alternatives or None, evidence=evidence,
        scope_path=item.get("scope_path"), recorded_by=recorded_by)
    (inbox_dir(cairn_dir) / f"{item_id}.json").unlink(missing_ok=True)
    return decision_id


def purge(cairn_dir: Path, now: datetime | None = None) -> int:
    """Drop proposals nobody looked at and old dismissals. Returns how many files were removed."""
    now = now or _now()
    removed = 0
    for item in _read_all(cairn_dir):
        keep = KEEP_DAYS if item["status"] == "pending" else KEEP_DISMISSED_DAYS
        stamp = item.get("last_seen") if item["status"] == "pending" else item.get("dismissed")
        try:
            age = now - datetime.fromisoformat(stamp or item["created"])
        except (ValueError, KeyError):
            continue
        if age > timedelta(days=keep):
            (inbox_dir(cairn_dir) / f"{item['id']}.json").unlink(missing_ok=True)
            removed += 1
    return removed


def summary(cairn_dir: Path) -> dict:
    pending = items(cairn_dir)
    return {"pending": len(pending), "ready": sum(1 for i in pending if i.get("seen", 1) >= READY_AT)}


def quote_in(evidence: str, transcript_text: str) -> bool:
    """True when the quote really occurs in the text (whitespace and case aside); a model's invented quote is dropped."""
    needle = " ".join((evidence or "").split()).casefold()
    return len(needle) >= 12 and needle in " ".join((transcript_text or "").split()).casefold()
