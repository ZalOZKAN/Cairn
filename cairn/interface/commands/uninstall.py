"""cairn uninstall: undo what `cairn init` and `cairn sync` wired into a project.

Two rules:
never delete what Cairn did not write (inside a shared file only Cairn's
marker-fenced block, the `cairn` MCP server entry and Cairn's own hook
commands go; every other server, hook and setting stays), and never leave an
empty shell (a file that held nothing but Cairn's part is deleted, not left
as `{}`). `.cairn/`, the decisions and memory themselves, is never touched.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import typer
from rich.console import Console

from cairn.interface.commands import mcp as mcp_cmd
from cairn.memory import agents_md

console = Console()


@dataclass
class Plan:
    removed: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _rel(project_dir: Path, p: Path) -> str:
    return p.relative_to(project_dir).as_posix()


def strip_block(text: str) -> str | None:
    """`text` without Cairn's marker-fenced block and the blank lines Cairn
    put around it, or None when there is no block. "" means nothing is left."""
    start = text.find(agents_md.BEGIN)
    end = text.find(agents_md.END, start + len(agents_md.BEGIN)) if start != -1 else -1
    if start == -1 or end == -1:
        return None
    before = text[:start].rstrip("\n")
    after = text[end + len(agents_md.END):].lstrip("\n")
    joined = before + ("\n\n" if before.strip() and after.strip() else "") + after
    return joined.rstrip("\n") + "\n" if joined.strip() else ""


def _retract_marked(project_dir: Path, p: Path, what: str, apply: bool, plan: Plan,
                    kept_note: str | None = None) -> bool:
    """Strip Cairn's block from a markdown file. True when the file is gone
    afterwards (deleted now, or never there)."""
    if not p.is_file():
        return True
    text, newline = agents_md._read(p)
    stripped = strip_block(text)
    if stripped is None:
        return False
    if stripped:
        plan.removed.append((_rel(project_dir, p), what))
        if kept_note:
            plan.notes.append(kept_note)
        if apply:
            p.write_text(stripped, encoding="utf-8", newline=newline)
        return False
    plan.removed.append((_rel(project_dir, p), f"{what} (nothing else in it: file deleted)"))
    if apply:
        p.unlink()
        _prune_dir(p.parent, project_dir)
    return True


def _remove_mcp_server(data: dict) -> list[str]:
    servers = data.get("mcpServers")
    if not isinstance(servers, dict) or "cairn" not in servers:
        return []
    del servers["cairn"]
    if not servers:
        del data["mcpServers"]
    return ["mcpServers.cairn"]


def _remove_cairn_hooks(data: dict) -> list[str]:
    """Cairn's own hook commands, from every event; a group or event left
    empty goes with them, any other hook in it stays."""
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return []
    touched = []
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            inner = group.get("hooks") if isinstance(group, dict) else None
            if isinstance(inner, list):
                kept = [h for h in inner
                        if not (isinstance(h, dict) and mcp_cmd.is_cairn_hook_command(str(h.get("command", ""))))]
                if len(kept) != len(inner):
                    touched.append(event)
                    if not kept:
                        continue
                    group["hooks"] = kept
            kept_groups.append(group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if touched and not hooks:
        del data["hooks"]
    return [f"hooks ({', '.join(dict.fromkeys(touched))})"] if touched else []


def _remove_gemini_agents_link(data: dict) -> list[str]:
    """AGENTS.md out of `context.fileName`. What is left being exactly the
    default (GEMINI.md) drops the key, which is how `link_gemini` found it."""
    context = data.get("context")
    if not isinstance(context, dict):
        return []
    names = context.get("fileName")
    listed = [names] if isinstance(names, str) else names
    if not isinstance(listed, list) or "AGENTS.md" not in listed:
        return []
    rest = [n for n in listed if n != "AGENTS.md"]
    if rest in ([], ["GEMINI.md"]):
        del context["fileName"]
    else:
        context["fileName"] = rest
    if not context:
        del data["context"]
    return ["context.fileName AGENTS.md"]


def _retract_json(project_dir: Path, p: Path, edits: list[Callable[[dict], list[str]]],
                  apply: bool, plan: Plan) -> None:
    if not p.is_file():
        return
    try:
        data = mcp_cmd._load_json_object(p)
    except ValueError:
        if "cairn" in p.read_text(encoding="utf-8", errors="replace"):
            plan.notes.append(f"{_rel(project_dir, p)} is not plain JSON, left alone: "
                              "remove Cairn's entries from it by hand")
        return
    labels = [label for edit in edits for label in edit(data)]
    if not labels:
        return
    if data:
        plan.removed.append((_rel(project_dir, p), ", ".join(labels)))
        if apply:
            p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return
    plan.removed.append((_rel(project_dir, p), f"{', '.join(labels)} (nothing else in it: file deleted)"))
    if apply:
        p.unlink()
        _prune_dir(p.parent, project_dir)


def _prune_dir(d: Path, project_dir: Path) -> None:
    if d != project_dir and d.is_dir() and not any(d.iterdir()):
        d.rmdir()


def uninstall(project_dir: Path, apply: bool) -> Plan:
    """What Cairn wired into `project_dir`, removed when `apply`, only listed
    otherwise. The list is the same either way."""
    plan = Plan()
    agents_gone = _retract_marked(project_dir, agents_md.path(project_dir), "Cairn's decisions block",
                                  apply, plan, kept_note="AGENTS.md kept: it has content that is not Cairn's")
    for rel in agents_md.CLAUDE_FILES:
        p = project_dir / rel
        text = agents_md._read(p)[0]
        what = "Cairn's scoped decisions block" if agents_md._has_thin_block(text) else "@AGENTS.md import"
        _retract_marked(project_dir, p, what, apply, plan)
    rules = agents_md._managed_rule_files(project_dir)
    if rules:
        plan.removed.append((agents_md.RULES_DIR.as_posix() + "/", f"{len(rules)} scoped rule file(s)"))
        if apply:
            for rule in rules:
                rule.unlink()
            rules_dir = project_dir / agents_md.RULES_DIR
            for d in (rules_dir, rules_dir.parent, rules_dir.parent.parent):
                _prune_dir(d, project_dir)
    _retract_json(project_dir, project_dir / ".mcp.json", [_remove_mcp_server], apply, plan)
    _retract_json(project_dir, project_dir / ".claude" / "settings.json", [_remove_cairn_hooks], apply, plan)
    gemini_edits = [_remove_mcp_server, _remove_cairn_hooks]
    # Nothing marks this entry as Cairn's, so it goes only when Cairn recorded
    # adding it, or when AGENTS.md is gone (then it changes nothing for Gemini
    # CLI); otherwise it may be the user's own and stays.
    recorded = agents_md.wired(project_dir, agents_md.GEMINI_LINK)
    if agents_gone or recorded:
        gemini_edits.append(_remove_gemini_agents_link)
    _retract_json(project_dir, project_dir / ".gemini" / "settings.json", gemini_edits, apply, plan)
    if apply and recorded:
        agents_md.record_wiring(project_dir, agents_md.GEMINI_LINK, added=False)
    return plan


def uninstall_cmd(
    yes: bool = typer.Option(False, "--yes", "-y", help="Remove for real (without it, only list what would go)"),
):
    """Remove what Cairn wired into this project: the AGENTS.md block, the CLAUDE.md
    import, MCP server entries and hooks. Keeps .cairn/ (your decisions)."""
    project_dir = Path.cwd()
    plan = uninstall(project_dir, apply=yes)
    if plan.removed:
        console.print("[bold]Removed:[/bold]" if yes else "[bold]Would remove:[/bold]")
        for path, what in plan.removed:
            console.print(f"  {path}: {what}", markup=False)
    else:
        console.print("Nothing of Cairn's is wired into this project.")
    for note in plan.notes:
        console.print(f"  note: {note}", markup=False)
    if (project_dir / ".cairn").is_dir():
        console.print("Kept .cairn/ (your decisions and memory); delete it yourself to remove them. "
                      "`cairn init` then `cairn sync` wires this project back up.", markup=False)
    console.print("Hosts set up by hand (e.g. Cursor, Antigravity, Codex user config) keep their "
                  "`cairn` entry; remove it there.", markup=False)
    if plan.removed and not yes:
        console.print("Run `cairn uninstall --yes` to remove the above.", markup=False)
