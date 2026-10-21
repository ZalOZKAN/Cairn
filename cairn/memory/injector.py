"""
ContextInjector: produce the session context to feed an AI at session start.


Layers (lazy, only present ones are included):
  L1: state.json          ~200 tokens  : active task, status
  L2: context-manifest    ~500 tokens  : project context
  L3: session-handoff     ~400 tokens  : where we left off
  L4: memory/*.md         on demand    : only when INDEX matches

Output formats:
  - "text"   : plain text the user can paste into any AI
  - "claude" : Claude Code @file syntax
  - "json"   : programmatic use
"""
from __future__ import annotations

import json
from pathlib import Path

from cairn.memory.manifest import ManifestManager
from cairn.memory.session import SessionManager

AGENT_GUIDE = (
    "Cairn keeps this project's decisions and carries them between tools. Every "
    "active decision is already in AGENTS.md (and in `memory_context`); follow them "
    "without looking them up again. Whenever a choice between options is settled "
    "(library, pattern, data model, API shape, trade-off), call `record_decision` "
    "with reasoning, the rejected alternatives, and `scope_path` when it applies to "
    "one part of the repo; if it replaces an earlier decision, pass `supersedes`. "
    "Call `check_approach` before trying something that may have been rejected "
    "before. If you stop with work unfinished, call `memory_save` with a short summary "
    "and the step still open (empty when the task is done, never an invented one), so "
    "the next session, in any tool, can continue. Saving the note ends your turn."
)


class ContextInjector:
    """
    Boot-context generator.
    Works with any AI: the `fmt` parameter controls the output shape.
    """

    def __init__(self, project_dir: Path | None = None):
        self.project_dir = project_dir or Path.cwd()
        self.session_mgr = SessionManager(self.project_dir)
        self.manifest_mgr = ManifestManager(self.project_dir)

    def build(
        self,
        fmt: str = "text",
        include_memory: list[str] | None = None,
        decisions: str | None = None,
    ) -> str:
        """
        Build the session context string.

        fmt:
          "text"   → pasteable into any AI
          "claude" → Claude Code @file references
          "json"   → machine-readable

        include_memory: extra memory files, e.g. ["decisions.md", "errors-solved.md"]
        """
        if fmt == "claude":
            return self._build_claude_format()
        if fmt == "json":
            return self._build_json_format(include_memory, decisions)
        return self._build_text_format(include_memory, decisions)

    # ── Format builders ────────────────────────────────────────────────

    def _build_text_format(
        self,
        include_memory: list[str] | None,
        decisions: str | None = None,
    ) -> str:
        """Pasteable-into-any-AI format."""
        sections = []

        sections.append("# Cairn: Session Boot Context")
        sections.append("*Read this context and continue from where we left off.*\n")

        # L1: State
        state = self.session_mgr.load_state()
        if state.get("current_task") or state.get("task_status") != "idle":
            sections.append("## Active Task")
            if state.get("current_task"):
                sections.append(f"Task: {state['current_task']}")
            sections.append(f"Status: {state.get('task_status', 'idle')}")
            if state.get("blockers"):
                sections.append(f"Blockers: {', '.join(state['blockers'])}")
            sections.append("")

        # L2: Manifest
        manifest = self.manifest_mgr.load()
        if manifest:
            sections.append(manifest)
            sections.append("")

        # L3: Handoff
        handoff = self.session_mgr.load_handoff()
        if handoff:
            sections.append(handoff)
            sections.append("")

        if decisions:
            sections.append(decisions)
            sections.append("")

        # L4: On-demand memory
        if include_memory:
            for fname in include_memory:
                content = self.session_mgr.load_memory_file(fname)
                if content:
                    sections.append(f"## Memory: {fname}\n{content}\n")

        sections.append("---")
        sections.append("*Context loaded. Ready to continue.*")
        sections.append("\n**REMINDER FOR THE AGENT:** " + AGENT_GUIDE)

        return "\n".join(sections)

    def _build_claude_format(self) -> str:
        """Claude Code @file reference format."""
        lines = ["# Session Start: paste this into Claude Code\n"]

        persistence = self.project_dir / ".cairn" / "persistence"

        if (persistence / "state.json").exists():
            lines.append(f"@{persistence / 'state.json'}")
        if (persistence / "context-manifest.md").exists():
            lines.append(f"@{persistence / 'context-manifest.md'}")
        if (persistence / "session-handoff.md").exists():
            lines.append(f"@{persistence / 'session-handoff.md'}")

        lines.append("\nContinue from where we left off based on the above context.")
        lines.append(AGENT_GUIDE)
        return "\n".join(lines)

    def _build_json_format(self, include_memory: list[str] | None, decisions: str | None = None) -> str:
        """JSON format: for SDK integration."""
        data = {
            "state": self.session_mgr.load_state(),
            "manifest": self.manifest_mgr.load(),
            "handoff": self.session_mgr.load_handoff(),
            "memory": {},
            "decisions": decisions or "",
        }
        if include_memory:
            for fname in include_memory:
                content = self.session_mgr.load_memory_file(fname)
                if content:
                    data["memory"][fname] = content

        return json.dumps(data, indent=2, ensure_ascii=False)

    def build_system_prompt_injection(self) -> str:
        """
        Short context to append to the system prompt inside Agent.run().
        Not the full session context, just active-task info.
        ~100-200 tokens.
        """
        state = self.session_mgr.load_state()
        handoff = self.session_mgr.load_handoff()

        parts = []

        if state.get("current_task"):
            parts.append(f"Current task: {state['current_task']} [{state.get('task_status', '')}]")

        if handoff:
            # Pull only the "Continuation Point" section
            for line in handoff.split("\n"):
                if line.startswith("## Continuation"):
                    continue
                if line.startswith("##"):
                    break
                if line.strip():
                    parts.append(line.strip())

        if not parts:
            return ""

        return "## Session Context\n" + "\n".join(parts)
