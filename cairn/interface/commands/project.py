"""Top-level project commands: sync, check, decide, handoff, doctor.

These are what an agent without MCP (or a human) uses from a shell; they do
the same thing as the matching MCP tools.
"""
from __future__ import annotations

import typer
from rich.console import Console

console = Console()


def _project(refresh_agents_md: bool = True):
    from cairn.core import Project
    project = Project(load_session=False, refresh_agents_md=refresh_agents_md)
    if project.memory.decisions_dir() is None:
        console.print("[red]No .cairn/ folder here; run `cairn init` first.[/red]")
        raise typer.Exit(code=1)
    return project


def link_agents_md(project) -> list[str]:
    """Add or refresh the AGENTS.md block and the CLAUDE.md import, importing
    any hand edits into the decision records first. Returns what changed, for
    printing."""
    from cairn.memory import agents_md
    if not project.memory.agents_md_enabled:
        return []
    changed = []
    result = agents_md.write(project.memory, create=True)
    if result["written"]:
        changed.append("AGENTS.md")
    for kind in ("invalidated", "superseded", "recorded"):
        if result[kind]:
            changed.append(f"{result[kind]} decision(s) {kind} from AGENTS.md edits")
    changed.extend(f"{name} (Claude Code scoped rules)" for name in result.get("claude", []))
    claude = agents_md.link_claude(project.project_dir)
    if claude is not None:
        changed.append(f"{claude.relative_to(project.project_dir).as_posix()} (imports AGENTS.md)")
    gemini = agents_md.link_gemini(project.project_dir)
    if gemini is not None:
        changed.append(f"{gemini.relative_to(project.project_dir).as_posix()} (Gemini CLI now reads AGENTS.md too)")
    return changed


def sync_cmd():
    """Sync .cairn/decisions/ with the index and rewrite the AGENTS.md block."""
    # refresh_agents_md=False: defer the AGENTS.md pass to link_agents_md()
    # below, so its counts (what this sync actually imported/wrote) are the
    # ones reported, instead of being silently absorbed by Project's own
    # eager sync on construction.
    project = _project(refresh_agents_md=False)
    changed = link_agents_md(project)
    if not project.memory.agents_md_enabled:
        console.print("[yellow]agents_md.enabled is false in config; AGENTS.md left alone.[/yellow]")
    elif changed:
        console.print(f"[green]Updated:[/green] {', '.join(changed)}", markup=False)
    else:
        console.print("[green]AGENTS.md is up to date[/green]")


def set_language(config_path, ui: str | None = None, agents: str | None = None) -> None:
    """Set `ui.language` and/or `memory.agents_md.language` in config.yaml,
    editing lines in place so the file's comments survive."""
    import re
    from pathlib import Path
    path = Path(config_path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def put(parent: re.Pattern, indent: str, value: str) -> bool:
        for i, line in enumerate(lines):
            if not parent.match(line):
                continue
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j].startswith(indent)):
                if re.match(rf"{indent}language:", lines[j]):
                    comment = lines[j].split("#", 1)[1] if "#" in lines[j] else None
                    lines[j] = f"{indent}language: {value}" + (f"  #{comment}" if comment else "")
                    return True
                j += 1
            lines.insert(i + 1, f"{indent}language: {value}")
            return True
        return False

    if ui and not put(re.compile(r"^ui:\s*(#.*)?$"), "  ", ui):
        lines += ["", "ui:", f"  language: {ui}"]
    if agents and not put(re.compile(r"^  agents_md:\s*(#.*)?$"), "    ", agents):
        # no agents_md block yet: open one right under `memory:`
        at = next((i for i, line in enumerate(lines) if re.match(r"^memory:\s*(#.*)?$", line)), None)
        if at is not None:
            lines[at + 1:at + 1] = ["  agents_md:", f"    language: {agents}"]
        elif any(line.startswith("memory:") for line in lines):
            # one-line `memory: {...}`: a second `memory:` key would silently
            # replace it, so leave it; AGENTS.md text falls back to ui.language
            if agents != ui:
                raise ValueError("memory: is written on one line in config.yaml; "
                                 "add agents_md: {language: ...} to it by hand")
        else:
            lines += ["", "memory:", "  agents_md:", f"    language: {agents}"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")


def lang_cmd(
    code: str = typer.Argument("", help="en, tr, fr, es, de, zh, ja; empty to show the current setting"),
    only: str = typer.Option("", "--only", help="ui (screens and help) or agents (AGENTS.md text); default both"),
):
    """Show or change Cairn's language (screens, help and the AGENTS.md text)."""
    from pathlib import Path
    from cairn.config.loader import load_config
    from cairn.interface.i18n import LANGUAGES, current_language
    from cairn.memory import agents_text
    config_path = Path.cwd() / ".cairn" / "config.yaml"
    if not code:
        cfg = load_config(config_path if config_path.exists() else None)
        console.print(f"ui (screens, help): {current_language(cfg)}")
        console.print(f"agents (AGENTS.md text): {agents_text.language(cfg)}")
        console.print("available: " + ", ".join(f"{k} ({v})" for k, v in LANGUAGES.items()))
        return
    code = code.strip().lower()[:2]
    if code not in LANGUAGES:
        console.print(f"[red]Unknown language {code!r}. Choose one of: {', '.join(LANGUAGES)}[/red]")
        raise typer.Exit(code=1)
    if not (Path.cwd() / ".cairn").is_dir():
        console.print("[red]No .cairn/ here; run `cairn init` first.[/red]")
        raise typer.Exit(code=1)
    try:
        set_language(config_path, ui=None if only == "agents" else code, agents=None if only == "ui" else code)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Language set to {LANGUAGES[code]}.[/green]")
    if only != "ui":
        project = _project(refresh_agents_md=False)
        link_agents_md(project)
        console.print("AGENTS.md rewritten in the new language.")


def check_cmd():
    """Exit 1 if the AGENTS.md block is missing or out of date (for CI)."""
    from cairn.memory import agents_md
    project = _project(refresh_agents_md=False)
    if project.memory.agents_md_enabled and agents_md.stale(project.memory):
        console.print("[red]AGENTS.md decisions block is missing or out of date; run `cairn sync`.[/red]")
        raise typer.Exit(code=1)
    console.print("[green]AGENTS.md is up to date[/green]")


def decide_cmd(
    decision: str = typer.Argument(..., help="The decision, in one sentence"),
    why: str = typer.Option("", "--why", "-w", help="Why this was chosen"),
    rejected: list[str] = typer.Option([], "--rejected", "-r", help="A rejected option (repeatable)"),
    because: list[str] = typer.Option([], "--because", "-b",
                                      help="Why the matching --rejected option lost (repeatable, same order)"),
    path: str = typer.Option("", "--path", "-p", help="Workspace-relative path it applies to; omit for repo-wide"),
    supersedes: str = typer.Option("", "--supersedes", help="Id of the decision this replaces"),
    replaces: str = typer.Option("", "--replaces",
                                 help="A phrase from the decision this replaces, if you don't have its id"),
    importance: int = typer.Option(2, "--importance", "-i", help="1=normal, 2=decision, 3=critical"),
    forbid: list[str] = typer.Option([], "--forbid",
                                     help="A regex that code added under this decision must not match; "
                                          "the PreToolUse hook blocks such an edit (repeatable)"),
):
    """Record a decision (the shell equivalent of the MCP record_decision tool)."""
    if len(because) > len(rejected):
        console.print("[red]More --because than --rejected values.[/red]")
        raise typer.Exit(code=1)
    alternatives = [
        {"option": opt, "rejected_because": because[i] if i < len(because) else ""}
        for i, opt in enumerate(rejected)
    ]
    project = _project()
    resolved_supersedes = supersedes or None
    if not resolved_supersedes and replaces:
        found = project.memory.resolve_replaces(replaces)
        if found["id"] is not None:
            resolved_supersedes = str(found["id"])
        elif found["candidates"]:
            console.print("[yellow]--replaces matched more than one decision; pass --supersedes with an id:[/yellow]")
            for c in found["candidates"]:
                console.print(f"  #{c['id']}: {c['decision']}", markup=False)
        else:
            console.print("[yellow]--replaces matched nothing; recording as a new decision.[/yellow]")
    try:
        new_id = project.memory.record_decision(
            decision, reasoning=why or None, alternatives=alternatives or None,
            supersedes=resolved_supersedes, importance=importance,
            scope_path=path or None, recorded_by="cli", forbid=forbid or None,
        )
    except ValueError as exc:
        console.print(str(exc), style="red", markup=False)
        raise typer.Exit(code=1)
    console.print(f"[green]Recorded decision #{new_id}[/green]")
    if resolved_supersedes is None:
        possible = project.memory.possible_supersedes(decision, path or None, exclude_id=new_id)
        if possible:
            console.print("[yellow]This may replace an existing decision; if so, invalidate it "
                          "(`cairn memory invalidate <id>`) so only one stays active:[/yellow]")
            for c in possible:
                console.print(f"  #{c['id']}: {c['decision']}", markup=False)


def handoff_cmd(
    summary: str = typer.Argument("", help="What was done this session"),
    next_step: str = typer.Option("", "--next", "-n", help="The next concrete step"),
):
    """Leave a note for the next session, in any tool (shell equivalent of MCP memory_save)."""
    from cairn.memory import handoff
    if not summary and not next_step:
        console.print("[red]Give a summary, --next, or both.[/red]")
        raise typer.Exit(code=1)
    project = _project()
    path = handoff.save_note(project, summary, next_step, source="cli")
    console.print("[green]Handoff written:[/green]", end=" ")
    console.print(str(path), markup=False, soft_wrap=True)


_HELPER_FEATURES = ("session extraction (`cairn memory save` without --summary)",
                    "manifest generation", "`cairn memory capture --from-git`", "conflict detection")


def doctor_cmd():
    """Show what Cairn does in this project, and what is off and why."""
    import importlib.util
    import json
    import os

    from cairn.memory import agents_md

    project = _project(refresh_agents_md=False)
    root = project.project_dir
    rows: list[tuple[str, str, str]] = []

    decisions = project.memory.decisions_dir()
    n = len(list(decisions.glob("*.md"))) if decisions.is_dir() else 0
    rows.append(("ok", "decisions", f"{n} decision files in .cairn/decisions/"))

    if not project.memory.agents_md_enabled:
        rows.append(("off", "AGENTS.md", "agents_md.enabled is false in config"))
    elif not agents_md.has_block(root):
        rows.append(("!!", "AGENTS.md", "no Cairn block, agents do not see the decisions; run `cairn sync`"))
    elif agents_md.stale(project.memory):
        rows.append(("!!", "AGENTS.md", "block is out of date; run `cairn sync`"))
    else:
        rows.append(("ok", "AGENTS.md", "block up to date (Antigravity, Cursor, Codex, Copilot read it)"))

    claude = next((root / rel for rel in agents_md.CLAUDE_FILES if (root / rel).exists()), None)
    if project.memory.claude_scoped_rules:
        n_rules = len(agents_md._managed_rule_files(root))
        rows.append(("!!" if agents_md._scoped_stale(project.memory) else "ok", "Claude Code",
                     f"scoped rules on: repo-wide decisions in CLAUDE.md, {n_rules} rule file(s) in "
                     f"{agents_md.RULES_DIR.as_posix()}/" + ("; out of date, run `cairn sync`"
                                                           if agents_md._scoped_stale(project.memory) else "")))
    elif claude is None:
        rows.append(("ok", "Claude Code", "no CLAUDE.md, so Claude Code (2.1.277+) reads AGENTS.md itself"))
    elif "AGENTS.md" in claude.read_text(encoding="utf-8"):
        rows.append(("ok", "Claude Code", f"{claude.relative_to(root).as_posix()} imports AGENTS.md"))
    else:
        rows.append(("!!", "Claude Code", f"{claude.relative_to(root).as_posix()} exists, so Claude Code skips "
                                          "AGENTS.md; run `cairn sync` to add the import"))

    gemini_settings = root / ".gemini" / "settings.json"
    gemini_data: dict = {}
    gemini_unreadable = False
    if gemini_settings.is_file():
        try:
            parsed = json.loads(gemini_settings.read_text(encoding="utf-8"))
            gemini_data = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            # Gemini CLI itself accepts comments here, which plain JSON does not.
            gemini_unreadable = True
    uses_gemini = gemini_settings.is_file() or (root / "GEMINI.md").exists()
    if not uses_gemini:
        rows.append(("--", "Gemini CLI", "no GEMINI.md or .gemini/ here; Gemini CLI reads only GEMINI.md by "
                                         "default, never AGENTS.md, until you use it in this project"))
    elif gemini_unreadable:
        rows.append(("!!", "Gemini CLI", ".gemini/settings.json is not plain JSON (comments?), so Cairn cannot "
                                         "read or edit it; make sure its context.fileName lists AGENTS.md"))
    else:
        context = gemini_data.get("context")
        names = (context.get("fileName") if isinstance(context, dict) else None) or []
        if isinstance(names, str):
            names = [names]
        if "AGENTS.md" in names:
            rows.append(("ok", "Gemini CLI", ".gemini/settings.json also loads AGENTS.md "
                                             "(Gemini CLI applies it only once this folder is trusted)"))
        else:
            rows.append(("!!", "Gemini CLI", "reads only GEMINI.md, not AGENTS.md; run `cairn sync` to add it "
                                             "to .gemini/settings.json's context.fileName"))
    if (root / "GEMINI.md").exists():
        rows.append(("ok", "GEMINI.md", "Antigravity reads it together with AGENTS.md; GEMINI.md wins on conflicts"))

    import shutil

    from cairn.interface.commands.mcp import claude_hook_status, detect_hosts, gemini_hook_status
    cairn_path = shutil.which("cairn") or "cairn"
    mcp_json = root / ".mcp.json"
    if not mcp_json.is_file():
        rows.append(("--", "MCP (Claude Code)", "no .mcp.json; run `cairn init` again to register"))
    else:
        try:
            servers = json.loads(mcp_json.read_text(encoding="utf-8")).get("mcpServers", {})
        except json.JSONDecodeError:
            servers = {}
        if "cairn" in servers:
            rows.append(("ok", "MCP (Claude Code)", ".mcp.json has cairn registered"))
        else:
            rows.append(("--", "MCP (Claude Code)", ".mcp.json exists but cairn is not in it"))

    status = claude_hook_status(root, cairn_path)
    missing = [e for e, present in status.items() if not present]
    if not missing:
        rows.append(("ok", "hooks (Claude Code)", "all 4 Cairn hooks present in .claude/settings.json"))
    elif len(missing) == len(status):
        rows.append(("--", "hooks (Claude Code)", "none configured; run `cairn init` again to register"))
    else:
        rows.append(("!!", "hooks (Claude Code)", f"missing: {', '.join(missing)}; run `cairn init` again"))

    if not gemini_settings.is_file():
        rows.append(("--", "MCP (Gemini CLI)", "no .gemini/settings.json; run `cairn init` again to register"))
    elif gemini_unreadable:
        rows.append(("--", "MCP (Gemini CLI)", ".gemini/settings.json is not plain JSON; merge "
                                               "`cairn mcp config --host gemini-cli --with-hooks` by hand"))
    else:
        g_servers = gemini_data.get("mcpServers")
        if isinstance(g_servers, dict) and "cairn" in g_servers:
            rows.append(("ok", "MCP (Gemini CLI)", ".gemini/settings.json has cairn registered"))
        else:
            rows.append(("--", "MCP (Gemini CLI)", ".gemini/settings.json exists but cairn is not in it"))
        if all(gemini_hook_status(root, cairn_path).values()):
            rows.append(("ok", "hooks (Gemini CLI)", "BeforeAgent decision detector present in .gemini/settings.json"))
        else:
            rows.append(("--", "hooks (Gemini CLI)", "no Cairn hook; run `cairn init` again to register"))

    other_hosts = [h for h in detect_hosts() if h not in ("claude-code", "gemini-cli")]
    if other_hosts:
        rows.append(("--", "other hosts", f"detected but not auto-registered: {', '.join(other_hosts)} "
                                          "(run `cairn mcp config --host <name>`)"))

    handoff_path = project.session.handoff_path
    if handoff_path.exists() and handoff_path.read_text(encoding="utf-8").strip():
        first = handoff_path.read_text(encoding="utf-8").strip().splitlines()[0].lstrip("# ")
        rows.append(("ok", "handoff", first))
    else:
        rows.append(("--", "handoff", "none yet; written by `memory_save`, `cairn handoff`, or when an MCP host closes"))

    model = project.config.get("memory_helper_model")
    if not model:
        rows.append(("off", "helper model", "not configured (not needed for decisions, AGENTS.md or handoff); "
                                            "off: " + "; ".join(_HELPER_FEATURES)))
    elif importlib.util.find_spec("litellm") is None:
        rows.append(("!!", "helper model", f"{model} configured but litellm is missing: pip install cairn[helper]"))
    elif str(model).startswith("groq/") and not os.environ.get("GROQ_API_KEY"):
        rows.append(("!!", "helper model", f"{model} configured but GROQ_API_KEY is not set"))
    else:
        rows.append(("ok", "helper model", f"{model} (reachability not tested)"))

    for mark, name, detail in rows:
        console.print(f"[{mark:>3}] {name:<13} {detail}", markup=False, soft_wrap=True)
