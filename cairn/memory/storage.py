"""Storage helpers shared by the Memory class and its parts: the SQLite
connection, DB path resolution, and small parsing helpers."""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def sqlite_connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


_SENTINEL = object()

LEGACY_SHARED_DB = "~/.cairn/memory.db"


def resolve_db_path(mem_cfg: dict, project_dir: Path | str | None = None) -> Path:
    """Project DB location. Explicit `db_path` wins (relative paths resolve
    against the project dir). Otherwise each project with a `.cairn/` folder
    gets its own DB, so decisions never leak between repos."""
    base = Path(project_dir) if project_dir else Path.cwd()
    explicit = mem_cfg.get("db_path")
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_absolute() else base / p
    if (base / ".cairn").is_dir():
        return base / ".cairn" / "persistence" / "memory.db"
    return Path(LEGACY_SHARED_DB).expanduser()


def forbid_to_json(patterns: list[str] | str | None, strict: bool = True) -> str | None:
    """Stored form of a decision's `forbid` regexes. A lone string is one
    pattern, never a sequence of one-character patterns. `strict` raises
    ValueError on the first one that does not compile; otherwise (reading a
    hand-edited decision file) it is dropped with a warning on stderr."""
    import json as _json
    import re as _re
    import sys as _sys
    if isinstance(patterns, str):
        patterns = [patterns]
    kept = []
    for p in patterns or []:
        if not isinstance(p, str) or not p.strip():
            continue
        try:
            _re.compile(p)
        except _re.error as exc:
            if strict:
                raise ValueError(f"forbid pattern {p!r} is not a valid regex: {exc}") from exc
            print(f"cairn: ignoring forbid pattern {p!r}, not a valid regex: {exc}", file=_sys.stderr)
            continue
        kept.append(p)
    return _json.dumps(kept, ensure_ascii=False) if kept else None



_UID_RE = re.compile(r"\d{8}-[0-9a-f]{6}")

# Decision blocks up to this many characters are sent in full; longer ones as an index.
AUTO_FULL_CHARS = 3200


INDEX_HEAD_CHARS = 120


def entry_header(display_id: int | str, kind: str, timestamp: str | None, scope: str = "project", flag: str = "") -> str:
    """`[#12 · decision · 2026-10-21]`: the line that names a recalled entry, in full and index output alike."""
    tag = " · global" if scope == "global" else ""
    return f"[#{display_id} · {kind} · {(timestamp or 'unknown')[:10]}{tag}{flag}]"


def index_line(header: str, text: str) -> str:
    """One line per entry: the header plus the first line of the text, cut at `INDEX_HEAD_CHARS`."""
    head = " ".join((text or "").split())
    if len(head) > INDEX_HEAD_CHARS:
        head = head[: INDEX_HEAD_CHARS - 3].rstrip() + "..."
    return f"{header} {head}"


def _as_utc(value: str) -> datetime:
    """Parse an ISO-8601 string; naive values are taken as UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
