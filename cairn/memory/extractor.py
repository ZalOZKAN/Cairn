"""
MemoryExtractor, extract structured knowledge from a conversation.

Instead of raw Q&A, it produces meaningful categories:
- facts: technical information learned
- decisions: decisions made and their rationale
- tasks: completed / in-progress work
- errors: errors that were solved
- next: continuation point for the next session

Calls the helper LLM, the extractor itself asks "what matters here?".
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cairn.core import Project

EXTRACTOR_PROMPT = """You are a memory extraction system for a developer AI assistant.

Analyze the conversation and extract structured knowledge. Be selective, only extract
information that would be genuinely useful in a FUTURE session.

Respond ONLY with valid JSON, no markdown fences:
{
  "facts": [
    "short factual statement worth remembering"
  ],
  "decisions": [
    {"decision": "what was decided", "reason": "why"}
  ],
  "tasks_completed": [
    "description of completed task"
  ],
  "tasks_in_progress": [
    {"task": "description", "status": "current state", "next_step": "what to do next"}
  ],
  "errors_solved": [
    {"error": "what the error was", "solution": "how it was fixed"}
  ],
  "next_session_start": "one sentence: exactly where to pick up next time"
}

Rules:
- facts: max 5 items, only non-obvious technical facts
- decisions: only architectural/design decisions, not trivial choices
- tasks_completed: brief descriptions, max 10
- tasks_in_progress: the most important unfinished work, max 3
- errors_solved: only if the pattern might recur
- next_session_start: the single most important continuation point
- If a category has nothing worth extracting, return empty array []
- Be concise, each item should be under 100 chars
"""


class MemoryExtractor:
    """
    Extracts structured memory from conversation history or raw text.
    """

    def __init__(self, project: "Project"):
        self.project = project

    def extract_from_session(self, session_entries: list[dict]) -> dict:
        """
        Extract memory from a list of session entries.
        Each entry: {"prompt": str, "response": str, "timestamp": str}
        """
        if not session_entries:
            return self._empty_extraction()

        conversation = self._format_conversation(session_entries)
        return self._run_extraction(conversation)

    def extract_from_text(self, text: str) -> dict:
        """Extract memory from raw text (for manual use)."""
        return self._run_extraction(text)

    def _run_extraction(self, conversation: str) -> dict:
        """Call the helper model to extract structured memory."""
        response = self.project.helper_call(
            prompt=f"Extract structured memory from this conversation:\n\n{conversation[:8000]}",
            system=EXTRACTOR_PROMPT,
        )
        if not response:
            return {**self._empty_extraction(), "_error": "helper model unavailable"}
        return self._parse_response(response)

    def _format_conversation(self, entries: list[dict]) -> str:
        """Render the entry list in a readable form."""
        lines = []
        for e in entries[-20:]:  # the last 20 entries are enough
            lines.append(f"USER: {e.get('prompt', '')[:300]}")
            lines.append(f"ASSISTANT: {e.get('response', '')[:500]}")
            lines.append("")
        return "\n".join(lines)

    def _parse_response(self, text: str) -> dict:
        """Parse the LLM output, tolerant of common issues."""
        clean = text.strip()
        for fence in ["```json", "```JSON", "```"]:
            if clean.startswith(fence):
                clean = clean[len(fence):]
                if clean.endswith("```"):
                    clean = clean[:-3]
                break
        try:
            data = json.loads(clean.strip())
            # schema validation
            return {
                "facts": data.get("facts", []),
                "decisions": data.get("decisions", []),
                "tasks_completed": data.get("tasks_completed", []),
                "tasks_in_progress": data.get("tasks_in_progress", []),
                "errors_solved": data.get("errors_solved", []),
                "next_session_start": data.get("next_session_start", ""),
                "_extracted_at": datetime.now(timezone.utc).isoformat(),
            }
        except json.JSONDecodeError:
            return {**self._empty_extraction(), "_raw": text[:500]}

    @staticmethod
    def _empty_extraction() -> dict:
        return {
            "facts": [],
            "decisions": [],
            "tasks_completed": [],
            "tasks_in_progress": [],
            "errors_solved": [],
            "next_session_start": "",
            "_extracted_at": datetime.now(timezone.utc).isoformat(),
        }
