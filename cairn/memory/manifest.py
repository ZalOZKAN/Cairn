"""
ManifestManager, creates and updates context-manifest.md.

Holds a stable, concise project context:
- stack, architecture, directory structure
- key files
- conventions

At every session start, it introduces the project in ~500 tokens.
Read this manifest instead of scanning the whole project.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cairn.core import Project

MANIFEST_GENERATOR_PROMPT = """You are a technical documentation assistant.

Analyze the project structure and create a concise context manifest.
This manifest will be read at the start of EVERY future AI session instead of
scanning the entire project, so it must be dense and accurate.

Target: ~400-500 tokens total. Be extremely concise.

Format exactly as follows (keep all headers):

## Project
Name: <name>
Stack: <main technologies, comma separated>
Purpose: <one sentence>

## Architecture
<2-4 bullet points about key architectural decisions>

## Key Files
<5-8 most important files with one-line descriptions>

## Conventions
<3-5 coding conventions used in this project>

## Current State
<1-2 sentences about current development state>
"""


class ManifestManager:
    """
    context-manifest.md management.
    """

    def __init__(self, project_dir: Path | None = None):
        self.project_dir = project_dir or Path.cwd()
        self.cairn_dir = self.project_dir / ".cairn"
        self.manifest_path = self.cairn_dir / "persistence" / "context-manifest.md"
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)

    def exists(self) -> bool:
        return self.manifest_path.exists()

    def load(self) -> str:
        """Return the manifest contents."""
        if self.manifest_path.exists():
            return self.manifest_path.read_text(encoding="utf-8")
        return ""

    def save(self, content: str) -> None:
        """Write the manifest contents."""
        header = f"# Context Manifest, {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n\n"
        self.manifest_path.write_text(header + content, encoding="utf-8")

    def generate(self, project: "Project") -> str:
        """Generate a manifest by scanning the project and calling the helper model."""
        project_info = self._scan_project()
        response = project.helper_call(
            prompt=f"Generate a context manifest for this project:\n\n{project_info}",
            system=MANIFEST_GENERATOR_PROMPT,
        )
        if response is None:
            return "# Manifest generation skipped, no helper model configured.\n"
        self.save(response)
        return response

    def update_section(self, section: str, content: str) -> None:
        """Update a specific section of the manifest."""
        manifest = self.load()
        if not manifest:
            return

        # Find the section and replace it
        lines = manifest.split("\n")
        new_lines = []
        in_section = False
        section_header = f"## {section}"

        for line in lines:
            if line.strip() == section_header:
                in_section = True
                new_lines.append(line)
                new_lines.append(content)
                continue
            if in_section and line.startswith("## "):
                in_section = False
            if not in_section:
                new_lines.append(line)

        self.manifest_path.write_text("\n".join(new_lines), encoding="utf-8")

    def _scan_project(self) -> str:
        """Scan the project structure, produce input for manifest generation."""
        lines = [f"Project directory: {self.project_dir.name}\n"]

        # File structure (2 levels)
        lines.append("## File Structure")
        structure = self._get_structure(self.project_dir, depth=2)
        lines.append(structure)

        # Read important files
        important_files = [
            "README.md", "pyproject.toml", "package.json",
            "requirements.txt", "setup.py", "Cargo.toml",
            ".cairn/SYSTEM.md",
        ]
        lines.append("\n## Key File Contents")
        for fname in important_files:
            fpath = self.project_dir / fname
            if fpath.exists():
                content = fpath.read_text(encoding="utf-8", errors="replace")[:800]
                lines.append(f"\n### {fname}\n{content}")

        return "\n".join(lines)[:6000]  # truncate before sending to the LLM

    def _get_structure(self, path: Path, depth: int, prefix: str = "") -> str:
        """Return the directory structure as text."""
        if depth == 0:
            return ""

        ignore = {
            ".git", "__pycache__", "node_modules", ".venv", "venv",
            "dist", "build", ".egg-info", ".cairn",
            "chroma", ".pytest_cache", "*.pyc",
        }

        lines = []
        try:
            items = sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name))
        except PermissionError:
            return ""

        for item in items[:30]:  # at most 30 items
            if item.name in ignore or item.name.startswith("."):
                continue
            if item.is_dir():
                lines.append(f"{prefix}{item.name}/")
                lines.append(self._get_structure(item, depth - 1, prefix + "  "))
            else:
                lines.append(f"{prefix}{item.name}")

        return "\n".join(filter(None, lines))
