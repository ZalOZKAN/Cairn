"""
ContextInjector, produce the boot context to feed an AI at session start.

Goal: ~1100 tokens of fixed boot cost (instead of 70-80k).

Layers (lazy, only present ones are included):
  L1: state.json          ~200 tokens , active task, status
  L2: context-manifest    ~500 tokens , project context
  L3: session-handoff     ~400 tokens , where we left off
  L4: memory/*.md         on demand   , only when INDEX matches

Output formats:
  - "text"   : plain text the user can paste into any AI
  - "claude" : Claude Code @file syntax
  - "json"   : programmatic use
"""
from __future__ import annotations

import json
from pathlib import Path

from cairn.memory.session import SessionManager
from cairn.memory.manifest import ManifestManager


class ContextInjector:
    """
    Boot-context generator.
    Works with any AI, the `fmt` parameter controls the output shape.
    """

    def __init__(self, project_dir: Path | None = None):
        self.project_dir = project_dir or Path.cwd()
        self.session_mgr = SessionManager(self.project_dir)
        self.manifest_mgr = ManifestManager(self.project_dir)

    def build(self, fmt: str = "text", include_memory: list[str] | None = None) -> str:
        """
        Build the boot inject string.

        fmt:
          "text"   → pasteable into any AI
          "claude" → Claude Code @file references
          "json"   → machine-readable

        include_memory: extra memory files, e.g. ["decisions.md", "errors-solved.md"]
        """
        if fmt == "claude":
            return self._build_claude_format()
        if fmt == "json":
            return self._build_json_format(include_memory)
        return self._build_text_format(include_memory)

    def estimate_tokens(self, fmt: str = "text") -> int:
        """Approximate token count."""
        content = self.build(fmt)
        return len(content) // 4

    # ── Format builders ────────────────────────────────────────────────

    def _build_text_format(self, include_memory: list[str] | None) -> str:
        """Pasteable-into-any-AI format."""
        sections = []

        sections.append("# Cairn, Session Boot Context")
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

        # L4: On-demand memory
        if include_memory:
            for fname in include_memory:
                content = self.session_mgr.load_memory_file(fname)
                if content:
                    sections.append(f"## Memory: {fname}\n{content}\n")

        sections.append("---")
        sections.append("*Context loaded. Ready to continue.*")

        return "\n".join(sections)

    def _build_claude_format(self) -> str:
        """Claude Code @file reference format."""
        lines = ["# Session Start, paste this into Claude Code\n"]

        persistence = self.project_dir / ".cairn" / "persistence"

        if (persistence / "state.json").exists():
            lines.append(f"@{persistence / 'state.json'}")
        if (persistence / "context-manifest.md").exists():
            lines.append(f"@{persistence / 'context-manifest.md'}")
        if (persistence / "session-handoff.md").exists():
            lines.append(f"@{persistence / 'session-handoff.md'}")

        lines.append("\nContinue from where we left off based on the above context.")
        return "\n".join(lines)

    def _build_json_format(self, include_memory: list[str] | None) -> str:
        """JSON format, for SDK integration."""
        data = {
            "state": self.session_mgr.load_state(),
            "manifest": self.manifest_mgr.load(),
            "handoff": self.session_mgr.load_handoff(),
            "memory": {},
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
        Not a full boot, just active-task info.
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
