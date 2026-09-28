"""
SessionManager: persist and load session state.

Each project keeps state under its own `.cairn/persistence/`:
- state.json          : active task, current status (~200 tokens)
- session-handoff.md  : last session summary (~400 tokens)
- memory/             : long-lived knowledge files

Implements the session-handoff pattern from `AGENT-README.md`.
Boot cost: ~1100 tokens (vs. 70-80k).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Default state schema
DEFAULT_STATE: dict[str, Any] = {
    "version": 1,
    "project": "",
    "current_task": None,
    "task_status": "idle",          # idle | planned | in_progress | review | done
    "last_session": None,
    "session_count": 0,
    "active_files": [],
    "blockers": [],
    "updated_at": None,
}


class SessionManager:
    """
    Session state management.

    Creates `.cairn/persistence/` inside the project directory.
    Uses `~/.cairn/global_state.json` for global memory.
    """

    def __init__(self, project_dir: Path | None = None):
        self.project_dir = project_dir or Path.cwd()
        self.persistence_dir = self.project_dir / ".cairn" / "persistence"
        self.global_dir = Path.home() / ".cairn" / "persistence"

        self.persistence_dir.mkdir(parents=True, exist_ok=True)
        self.global_dir.mkdir(parents=True, exist_ok=True)

        self.state_path = self.persistence_dir / "state.json"
        self.handoff_path = self.persistence_dir / "session-handoff.md"
        self.memory_dir = self.persistence_dir / "memory"
        self.memory_dir.mkdir(exist_ok=True)

    # ── State read/write ───────────────────────────────────────────────

    def load_state(self) -> dict:
        """Read state.json. Return defaults if missing."""
        if self.state_path.exists():
            try:
                with open(self.state_path, encoding="utf-8") as f:
                    data = json.load(f)
                # fill in missing keys with defaults
                return {**DEFAULT_STATE, **data}
            except (json.JSONDecodeError, OSError):
                pass
        return {**DEFAULT_STATE, "project": self.project_dir.name}

    def save_state(self, state: dict) -> None:
        """Write state.json."""
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)

    def update_state(self, **kwargs) -> dict:
        """Partially update the current state."""
        state = self.load_state()
        state.update(kwargs)
        self.save_state(state)
        return state

    # ── Session begin / end ────────────────────────────────────────────

    def begin_session(self) -> dict:
        """Called at the start of a session. Loads state, bumps session counter."""
        state = self.load_state()
        state["session_count"] = state.get("session_count", 0) + 1
        state["last_session"] = datetime.now(timezone.utc).isoformat()
        self.save_state(state)
        return state

    def end_session(self, extraction: dict, summary: str = "") -> dict:
        """
        Called at end of session.
        extraction: structured info from MemoryExtractor
        summary: optional free-text summary
        """
        state = self.load_state()

        # tasks_in_progress → write to state
        if extraction.get("tasks_in_progress"):
            top_task = extraction["tasks_in_progress"][0]
            state["current_task"] = top_task.get("task", "")
            state["task_status"] = "in_progress"

        # Errors → append to memory/errors-solved.md
        if extraction.get("errors_solved"):
            self._append_memory("errors-solved.md", extraction["errors_solved"], "error")

        # Decisions → append to memory/decisions.md
        if extraction.get("decisions"):
            self._append_memory("decisions.md", extraction["decisions"], "decision")

        # Facts → append to memory/facts.md
        if extraction.get("facts"):
            self._append_memory("facts.md", extraction["facts"], "fact")

        # Write handoff markdown
        self._write_handoff(extraction, summary)

        # Update state
        state["last_handoff"] = datetime.now(timezone.utc).isoformat()
        self.save_state(state)

        return state

    # ── Handoff file ───────────────────────────────────────────────────

    def _write_handoff(self, extraction: dict, summary: str) -> None:
        """Write session-handoff.md."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"# Session Handoff: {now}\n"]

        if extraction.get("next_session_start"):
            lines.append(f"## Continuation Point\n{extraction['next_session_start']}\n")

        if extraction.get("tasks_in_progress"):
            lines.append("## In-Progress Tasks")
            for t in extraction["tasks_in_progress"]:
                lines.append(f"- **{t.get('task', '')}**")
                lines.append(f"  Status: {t.get('status', '')}")
                lines.append(f"  Next: {t.get('next_step', '')}")
            lines.append("")

        if extraction.get("tasks_completed"):
            lines.append("## Completed This Session")
            for t in extraction["tasks_completed"]:
                lines.append(f"- {t}")
            lines.append("")

        if summary:
            lines.append(f"## Summary\n{summary}\n")

        self.handoff_path.write_text("\n".join(lines), encoding="utf-8")

    def load_handoff(self) -> str:
        """Return the contents of session-handoff.md."""
        if self.handoff_path.exists():
            return self.handoff_path.read_text(encoding="utf-8")
        return ""

    # ── Long-lived memory files ────────────────────────────────────────

    def _append_memory(self, filename: str, items: list, item_type: str) -> None:
        """Append new entries to a memory file."""
        path = self.memory_dir / filename
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [f"\n## [{now}]"]

        for item in items:
            if isinstance(item, dict):
                if item_type == "decision":
                    lines.append(f"- **{item.get('decision', '')}**")
                    lines.append(f"  Rationale: {item.get('reason', '')}")
                elif item_type == "error":
                    lines.append(f"- Error: {item.get('error', '')}")
                    lines.append(f"  Solution: {item.get('solution', '')}")
            else:
                lines.append(f"- {item}")

        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def load_memory_file(self, filename: str) -> str:
        """Read a specific memory file."""
        path = self.memory_dir / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def list_memory_files(self) -> list[str]:
        """List files inside the memory directory."""
        return [f.name for f in self.memory_dir.iterdir() if f.suffix == ".md"]
