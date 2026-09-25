"""Cross-project registry of local Cairn projects.

Lives at ~/.cairn/projects.json (sibling to global.db). `cairn init` and
dashboard generation each record the current project's absolute path so a
project's dashboard can list and link to sibling Cairn projects on the same
machine, without ever merging their memory data together: each project's
`.cairn/persistence/memory.db` stays isolated, this registry only tracks
"a Cairn project exists here" and "when was it last touched."
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def register_project(project_dir: Path, registry_path: Path) -> None:
    """Record `project_dir` in the registry, updating `last_seen` if it's
    already there. Best-effort: a corrupt or unwritable registry never
    blocks `init`/`dashboard` from completing."""
    project_dir = project_dir.resolve()
    try:
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        projects = _read(registry_path)
        # Re-insert at the end so file order is registration recency, the
        # tie-break list_projects falls back on when timestamps collide.
        projects.pop(str(project_dir), None)
        projects[str(project_dir)] = {
            "path": str(project_dir),
            "name": project_dir.name,
            "last_seen": datetime.now(timezone.utc).isoformat(),
        }
        registry_path.write_text(
            json.dumps(projects, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass


def list_projects(registry_path: Path) -> list[dict]:
    """All registered projects, most recently seen first."""
    projects = _read(registry_path)
    # Coarse clocks (~15.6 ms on Windows before Python 3.13) can give two quick
    # registrations the same last_seen; reversed() + stable sort puts the later
    # registration first among equals.
    return sorted(reversed(list(projects.values())), key=lambda p: p["last_seen"], reverse=True)


def _read(registry_path: Path) -> dict:
    if not registry_path.exists():
        return {}
    try:
        return json.loads(registry_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
