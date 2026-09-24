"""Local usage counters: `.cairn/persistence/stats.json`.

Never sent anywhere. They exist so a dogfooding period can be judged on
numbers: is the agent recording decisions, and do recalls/checks find anything?
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

STATS_FILENAME = "stats.json"


def stats_path(project_dir: Path) -> Path | None:
    cairn_dir = project_dir / ".cairn"
    return cairn_dir / "persistence" / STATS_FILENAME if cairn_dir.is_dir() else None


def bump(project_dir: Path, tool: str, hit: bool | None = None) -> None:
    """Count one call of `tool`; `hit` records whether it returned anything.
    Best-effort: a stats failure must never break the tool call."""
    path = stats_path(project_dir)
    if path is None:
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        data = {}
    now = datetime.now(timezone.utc).isoformat()
    data.setdefault("since", now)
    data["updated"] = now
    entry = data.setdefault("tools", {}).setdefault(tool, {"calls": 0, "hits": 0})
    entry["calls"] += 1
    if hit:
        entry["hits"] += 1
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def load(project_dir: Path) -> dict:
    path = stats_path(project_dir)
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
