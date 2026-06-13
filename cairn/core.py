"""
Cairn core, Project facade.

Cairn is NOT an LLM runtime in v0.4. It is a portable `.cairn/`
folder that any AI coding agent (Claude Code, Cursor, Antigravity, custom
CLI) can read from and write to. The `Project` class is a thin convenience
wrapper that:

- loads `.cairn/config.yaml` (or falls back to defaults),
- exposes the `Memory` instance,
- lists skills / agents / workflows shipped with the package or overridden
  by the project,
- gives a single `helper_call(prompt, system)` for the few features that
  legitimately need a small LLM (extractor, manifest generation, conflict
  detection, summarization). The helper model is configurable.

That is the entire runtime surface. Everything else lives in markdown files
that travel with the project.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from cairn.config.loader import load_config
from cairn.memory.memory import Memory
from cairn.memory.session import SessionManager
from cairn.memory.injector import ContextInjector
from cairn.skills.registry import SkillRegistry
from cairn.security.guard import SecurityGuard


class Project:
    """Thin facade over a `.cairn/` folder."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        project_dir: Path | None = None,
        load_session: bool = True,
    ):
        self.project_dir = project_dir or Path.cwd()
        # If no explicit config_path and the project dir has one, use it.
        if config_path is None:
            candidate = self.project_dir / ".cairn" / "config.yaml"
            if candidate.exists():
                config_path = candidate
        self.config: dict[str, Any] = load_config(config_path)
        self.memory = Memory(self.config)
        self.skills = SkillRegistry(self.config)
        self.security = SecurityGuard(self.config)
        self.session = SessionManager(self.project_dir)
        self.injector = ContextInjector(self.project_dir)
        if load_session:
            self.session.begin_session()

    # ---- helper LLM ----------------------------------------------------

    def helper_call(self, prompt: str, system: str | None = None) -> str | None:
        """Run the single configured helper model. Returns None if disabled
        or unavailable. This is the ONLY place Cairn itself calls an LLM."""
        model = self.config.get("memory_helper_model")
        if not model:
            return None
        try:
            import litellm
        except ImportError:
            return None
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            resp = litellm.completion(model=model, messages=messages, max_tokens=1500)
            return (resp.choices[0].message.content or "").strip() or None
        except Exception:
            return None

    # ---- skills/agents/workflows: read-only file format ----------------

    def list_skills(self) -> list[dict]:
        return self.skills.list_skills()

    def get_skill(self, alias_or_name: str) -> str | None:
        return self.skills.load(alias_or_name)

    def list_agents(self) -> list[str]:
        return _list_md(_pkg_dir("agents"), self.project_dir / ".cairn" / "agents")

    def get_agent(self, name: str) -> str | None:
        return _read_md(name, _pkg_dir("agents"), self.project_dir / ".cairn" / "agents")

    def list_workflows(self) -> list[str]:
        return _list_md(_pkg_dir("workflows"), self.project_dir / ".cairn" / "workflows")

    def get_workflow(self, name: str) -> str | None:
        name = name.lstrip("/")
        return _read_md(name, _pkg_dir("workflows"), self.project_dir / ".cairn" / "workflows")

    # ---- lifecycle ----------------------------------------------------

    def close(self) -> None:
        self.memory.close()


# Backwards-compatible alias, older code did `from cairn import Agent`.
# The class is no longer an LLM runtime; it's a memory + file-format facade.
Agent = Project


# --------------------------- helpers ---------------------------------

def _pkg_dir(name: str) -> Path:
    return Path(__file__).parent / name


def _list_md(*dirs: Path) -> list[str]:
    names: dict[str, None] = {}
    for d in dirs:
        if d.exists():
            for p in sorted(d.glob("*.md")):
                names[p.stem] = None
    return list(names.keys())


def _read_md(name: str, *dirs: Path) -> str | None:
    for d in dirs:
        p = d / f"{name}.md"
        if p.exists():
            return p.read_text(encoding="utf-8")
    return None
