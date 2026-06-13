"""
Workflow loader, markdown only.

In v0.4 workflows are no longer executed by Cairn. A workflow is a
markdown system-prompt template; the runner resolves ``/name`` to its file
path and returns the markdown. The host is responsible for executing it.
"""
from __future__ import annotations

from pathlib import Path


WORKFLOWS_DIR = Path(__file__).parent


class WorkflowRunner:
    def __init__(self, project_dir: Path | None = None):
        self._project_dir = project_dir or Path.cwd()

    def load(self, name_or_command: str) -> str | None:
        """Return workflow markdown content, or None if not found.

        Accepts ``/plan``, ``plan``, or ``/plan some args``, anything after the
        first whitespace is ignored (the host can re-attach the args).
        """
        token = name_or_command.strip().split(None, 1)[0]
        name = token.lstrip("/")
        custom = self._project_dir / ".cairn" / "workflows" / f"{name}.md"
        if custom.exists():
            return custom.read_text(encoding="utf-8")
        builtin = WORKFLOWS_DIR / f"{name}.md"
        if builtin.exists():
            return builtin.read_text(encoding="utf-8")
        return None

    def list_workflows(self) -> list[str]:
        names: dict[str, None] = {}
        custom_dir = self._project_dir / ".cairn" / "workflows"
        for d in (custom_dir, WORKFLOWS_DIR):
            if d.exists():
                for p in sorted(d.glob("*.md")):
                    names[p.stem] = None
        return list(names.keys())
