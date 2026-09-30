"""Memory bridge: import what another tool's own persistent memory already
learned, so it becomes a Cairn decision every host can then see through
AGENTS.md. Nothing is imported silently: `cairn memory import-native` shows
every candidate and asks, the same "propose, human confirms" pattern as
`import_adr` and `capture --from-git`. Re-running is a no-op for anything
whose text has not changed since it was imported (matched by `recorded_by`
plus a content hash kept in the decision file, not by re-asking every time).

Formats:
- Claude Code auto-memory: `~/.claude/projects/<encoded-cwd>/memory/MEMORY.md`
  (an index of one-line hooks) plus one file per fact (YAML front matter +
  body, optionally `**Why:**` / `**How to apply:**` sections). The directory
  name encodes the project path by replacing every non-alphanumeric character
  with `-` (verified against a real folder Claude Code produced for this
  project: `C:\\Projects\\Cairn` -> `C--Projects-Cairn`).
- Cursor rules: `.cursor/rules/*.md` / `*.mdc`, YAML front matter with
  `description` (and `globs` / `alwaysApply`, read but not otherwise used
  here). Built from Cursor's documented format (cursor.com/docs/rules); not
  verified against a live Cursor install.
- Gemini CLI (0.61.0): no save-memory tool; the model edits markdown files
  itself and routes each fact by its phrasing into one of four tiers (read from
  the CLI's own system prompt, 2026-09-29; the global tier confirmed by a live
  sample, a `- <topic>: <preference>` bullet). Two tiers are imported: the
  project's `GEMINI.md` (team conventions, already committed) as project
  decisions, and the personal `~/.gemini/GEMINI.md` as Cairn global decisions,
  so a personal preference never lands in a repo's committed files. Not
  imported: subdirectory `GEMINI.md` files, and the private project memory
  under `~/.gemini/tmp/<id>/memory/`, which Gemini keeps out of the repo by
  definition while Cairn's project decisions are committed files. Top-level
  bullets only, outside fenced code and outside tool-managed marker blocks
  (Cairn's and other tools'). An earlier version parsed a `## Gemini Added Memories`
  heading the real product does not have; that parser is gone.

Every source produces the same shape: `{"decision", "reasoning", "source",
"origin"}`, plus `"scope": "global"` for a cross-project item. `source` becomes `recorded_by` (e.g. "claude-code-memory",
"cursor-rules") so provenance survives into AGENTS.md
and `.cairn/decisions/*.md` exactly like any other decision.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


def _one_line(text: str | None) -> str:
    return " ".join((text or "").split())


# ---- Claude Code auto-memory --------------------------------------------

_INDEX_LINE = re.compile(r"^-\s*\[([^\]]+)\]\(([^)]+)\)\s*[:\u2014-]\s*(.+)$")
_WHY_SECTION = re.compile(r"\*\*Why:\*\*\s*(.+?)(?:\n\n|\Z)", re.DOTALL)


def claude_memory_dir(project_dir: Path, claude_home: Path | None = None) -> Path:
    """`~/.claude/projects/<encoded-project_dir>/memory/`."""
    encoded = re.sub(r"[^a-zA-Z0-9]", "-", str(project_dir.resolve()))
    home = claude_home or (Path.home() / ".claude")
    return home / "projects" / encoded / "memory"


def parse_claude_memory(memory_dir: Path) -> list[dict[str, Any]]:
    """One entry per fact file linked from `MEMORY.md`, skipping any line
    that is not a well-formed index entry rather than failing the whole
    import over one hand-edited line."""
    index = memory_dir / "MEMORY.md"
    if not index.is_file():
        return []
    out = []
    for line in index.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _INDEX_LINE.match(line.strip())
        if not m:
            continue
        _title, rel, hook = m.groups()
        fact_path = memory_dir / rel
        if not fact_path.is_file():
            continue
        text = fact_path.read_text(encoding="utf-8", errors="replace")
        front, body = {}, text
        if text.startswith("---\n"):
            end = text.find("\n---", 4)
            if end != -1:
                try:
                    front = yaml.safe_load(text[4:end]) or {}
                except yaml.YAMLError:
                    front = {}
                body = text[end + 4:]
        why = _WHY_SECTION.search(body)
        out.append({
            "decision": _one_line(hook) or _one_line(front.get("description")),
            "reasoning": _one_line(why.group(1)) if why else None,
            "source": "claude-code-memory",
            "origin": rel,
        })
    return out


# ---- Cursor rules ---------------------------------------------------------

def parse_cursor_rules(project_dir: Path) -> list[dict[str, Any]]:
    """Not verified against a live Cursor install (cursor.com/docs/rules,
    2026-09-27). `alwaysApply: false` rules with a `globs` pattern are file-
    scoped guidance an agent reads on demand, closer to a style guide than a
    settled decision, so only `alwaysApply: true` rules (or ones with no
    front matter at all, which Cursor always applies) are imported."""
    rules_dir = project_dir / ".cursor" / "rules"
    if not rules_dir.is_dir():
        return []
    out = []
    for f in sorted(rules_dir.glob("*.md")) + sorted(rules_dir.glob("*.mdc")):
        text = f.read_text(encoding="utf-8", errors="replace")
        front, body = {}, text
        if text.startswith("---\n"):
            end = text.find("\n---", 4)
            if end != -1:
                try:
                    front = yaml.safe_load(text[4:end]) or {}
                except yaml.YAMLError:
                    front = {}
                body = text[end + 4:]
        if front and not front.get("alwaysApply", False):
            continue
        decision = _one_line(front.get("description")) or _one_line(body.splitlines()[0] if body.strip() else "")
        if not decision:
            continue
        out.append({"decision": decision, "reasoning": None, "source": "cursor-rules", "origin": f.name})
    return out


# ---- Gemini CLI -----------------------------------------------------------

_BULLET = re.compile(r"^[-*]\s+(.+)$")
_MANAGED = re.compile(r"<!--\s*[\w.:-]+?:(?:start|begin)\s*-->.*?<!--\s*[\w.:-]+?:end\s*-->", re.DOTALL)


def gemini_bullets(text: str) -> list[str]:
    """Top-level bullets of a GEMINI.md, outside fenced code and outside any
    tool-managed marker block: what a person or the model wrote there as a
    standing instruction. Nested bullets are detail, not separate facts."""
    text = _MANAGED.sub("", text.replace("\r\n", "\n"))
    out, fenced = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        m = None if fenced else _BULLET.match(line)
        if m and _one_line(m.group(1)):
            out.append(_one_line(m.group(1)))
    return out


def parse_gemini_memory(project_dir: Path, gemini_home: Path | None = None) -> list[dict[str, Any]]:
    home = gemini_home or (Path.home() / ".gemini")
    out = []
    for path, scope, source in ((project_dir / "GEMINI.md", "project", "gemini-project"),
                                (home / "GEMINI.md", "global", "gemini-global")):
        if not path.is_file():
            continue
        for bullet in gemini_bullets(path.read_text(encoding="utf-8", errors="replace")):
            item = {"decision": bullet, "reasoning": None, "source": source, "origin": path.name}
            if scope == "global":
                item["scope"] = "global"
            out.append(item)
    return out


PARSERS = {
    "claude": lambda project_dir: parse_claude_memory(claude_memory_dir(project_dir)),
    "cursor": parse_cursor_rules,
    "gemini": parse_gemini_memory,
}


def candidates(project_dir: Path, sources: list[str] | None = None,
               global_db: Path | None = None) -> list[dict[str, Any]]:
    """Every importable item across the requested sources (default: all),
    minus ones already imported with unchanged text (by `recorded_by` + the
    same `content_hash` every decision is stored with, so a re-run is a
    no-op unless the source text actually changed). A global item is checked
    against `global_db`, where it would have been imported to."""
    from cairn.memory.memory import Memory, sqlite_connect

    items = []
    for name in sources or PARSERS:
        items.extend(PARSERS[name](project_dir))

    def seen_in(db_path: Path | None) -> set[tuple[str, str]]:
        if db_path is None or not db_path.is_file():
            return set()
        with sqlite_connect(db_path) as conn:
            rows = conn.execute(
                "SELECT recorded_by, content_hash FROM memories WHERE kind='decision'"
            ).fetchall()
        return {(r, h) for r, h in rows if r}

    seen = {"project": seen_in(project_dir / ".cairn" / "persistence" / "memory.db"), "global": seen_in(global_db)}
    return [
        i for i in items
        if (i["source"], Memory._content_hash(i["decision"], i["reasoning"] or ""))
        not in seen[i.get("scope", "project")]
    ]
