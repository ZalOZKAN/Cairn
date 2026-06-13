"""
Skill registry, markdown loader.

In v0.4 Cairn does not execute skills. A skill is a system-prompt
template written in markdown. The registry resolves aliases to file paths
and returns the markdown content. The host (Claude Code, Cursor, your CLI,
etc.) is responsible for actually feeding it to a model.
"""
from __future__ import annotations

from pathlib import Path


SKILL_ALIASES: dict[str, str] = {
    "@pp":  "prompt-pro",
    "@cr":  "code-review",
    "@sd":  "systematic-debug",
    "@py":  "py-dev",
    "@sec": "security-audit",
    "@cc":  "clean-code",
    "@doc": "doc-templates",
    "@api": "api-design",
}

BUILTIN_SKILLS_DIR = Path(__file__).parent


class SkillRegistry:
    def __init__(self, config: dict | None = None, project_dir: Path | None = None):
        self.config = config or {}
        project_dir = project_dir or Path.cwd()
        self._custom_dir = project_dir / ".cairn" / "skills"

    def load(self, alias_or_name: str) -> str | None:
        """Return the skill's markdown content, or None if not found.

        Accepts either an alias like ``@cr`` or a folder name like ``code-review``.
        Custom (project-local) skills override built-ins.
        """
        token = alias_or_name.strip()
        name = SKILL_ALIASES.get(token, token.lstrip("@"))
        custom_path = self._custom_dir / name / "SKILL.md"
        if custom_path.exists():
            return custom_path.read_text(encoding="utf-8")
        builtin_path = BUILTIN_SKILLS_DIR / name / "SKILL.md"
        if builtin_path.exists():
            return builtin_path.read_text(encoding="utf-8")
        return None

    def list_skills(self) -> list[dict]:
        result = []
        for alias, name in SKILL_ALIASES.items():
            custom = (self._custom_dir / name / "SKILL.md").exists()
            builtin = (BUILTIN_SKILLS_DIR / name / "SKILL.md").exists()
            result.append({
                "alias": alias,
                "name": name,
                "available": custom or builtin,
                "source": "custom" if custom else ("builtin" if builtin else "missing"),
            })
        return result
