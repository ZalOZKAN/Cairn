"""Lessons: small things learned while working ("the test DB must be reset before this suite"), tied to the files they
concern. They are shown only when an agent touches one of those files, never loaded all the time.

A lesson the user stated does not fade; one the agent inferred loses a point of confidence every 30 days. A lesson
whose files no longer exist is reported as stale.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from cairn.memory.dedup import candidate_relation
from cairn.security.guard import mask_secrets

TYPES = ("pitfall", "pattern", "preference")
ORIGINS = ("user-stated", "observed", "inferred")
FADE_DAYS = 30
MAX_TEXT = 500


def lessons_dir(cairn_dir: Path) -> Path:
    return Path(cairn_dir) / "lessons"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value, limit: int) -> str:
    return mask_secrets(" ".join(str(value or "").split()))[:limit]


def _all(cairn_dir: Path) -> list[dict]:
    out = []
    for f in sorted(lessons_dir(cairn_dir).glob("*.json")):
        try:
            item = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(item, dict) and item.get("id") == f.stem:
            out.append(item)
    return out


def _save(cairn_dir: Path, item: dict) -> None:
    lessons_dir(cairn_dir).mkdir(parents=True, exist_ok=True)
    (lessons_dir(cairn_dir) / f"{item['id']}.json").write_text(json.dumps(item, ensure_ascii=False, indent=1), encoding="utf-8")


def record(cairn_dir: Path, text: str, files: list[str], *, kind: str = "pitfall", origin: str = "observed", confidence: int = 6) -> dict:
    """Store a lesson, or raise its `seen` count when the same one is already there."""
    text = _clean(text, MAX_TEXT)
    files = [f.replace("\\", "/").strip("/") for f in (_clean(f, 300) for f in files) if f][:8]
    if not text or not files:
        raise ValueError("a lesson needs a text and at least one file")
    if kind not in TYPES or origin not in ORIGINS or not 1 <= confidence <= 10:
        raise ValueError(f"type is one of {', '.join(TYPES)}, origin one of {', '.join(ORIGINS)}, confidence 1 to 10")
    for item in _all(cairn_dir):
        if text.casefold() == item["text"].casefold() or candidate_relation(text, item["text"])[0] == "possible-duplicate":
            item["seen"] = item.get("seen", 1) + 1
            item["files"] = sorted(set(item["files"]) | set(files))[:8]
            item["updated"] = _now().isoformat()
            _save(cairn_dir, item)
            return item
    item = {"id": f"{_now().strftime('%Y%m%d')}-{secrets.token_hex(3)}", "created": _now().isoformat(), "updated": _now().isoformat(),
            "text": text, "kind": kind, "origin": origin, "confidence": confidence, "files": files, "seen": 1}
    _save(cairn_dir, item)
    return item


def confidence_now(item: dict, now: datetime | None = None) -> int:
    """Stated lessons keep their confidence; the others lose a point per FADE_DAYS since they were last updated."""
    if item.get("origin") == "user-stated":
        return item["confidence"]
    age = (now or _now()) - datetime.fromisoformat(item.get("updated") or item["created"])
    return max(0, item["confidence"] - age.days // FADE_DAYS)


def _touches(item: dict, path: str) -> bool:
    path = path.replace("\\", "/").strip("/")
    return any(path == f or path.startswith(f.rstrip("/") + "/") or f.startswith(path.rstrip("/") + "/") for f in item["files"])


def for_path(cairn_dir: Path, path: str, now: datetime | None = None) -> list[dict]:
    """Lessons for one file or folder, most confident first; faded ones (confidence 0) are left out."""
    rows = [(confidence_now(i, now), i) for i in _all(cairn_dir) if _touches(i, path)]
    return [i for c, i in sorted(rows, key=lambda r: (-r[0], -r[1].get("seen", 1))) if c > 0]


def stale(cairn_dir: Path, project_dir: Path) -> list[dict]:
    """Lessons none of whose files exist any more."""
    return [i for i in _all(cairn_dir) if not any((Path(project_dir) / f).exists() for f in i["files"])]


def forget(cairn_dir: Path, lesson_id: str) -> bool:
    if not re.fullmatch(r"[0-9]{8}-[0-9a-f]{6}", lesson_id or ""):
        return False
    path = lessons_dir(cairn_dir) / f"{lesson_id}.json"
    if not path.is_file():
        return False
    path.unlink()
    return True
