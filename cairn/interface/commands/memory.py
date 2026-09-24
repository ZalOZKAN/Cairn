"""cairn memory, session memory management."""
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from pathlib import Path

memory_app = typer.Typer(help="Session memory management")
console = Console()


@memory_app.command("save")
def memory_save(
    summary: str = typer.Option("", "--summary", "-s", help="Manual summary (optional)"),
    no_extract: bool = typer.Option(False, "--no-extract", help="Skip LLM extraction"),
):
    """End of session: store memory, write handoff."""
    from cairn.core import Agent
    from cairn.memory.session import SessionManager
    from cairn.memory.extractor import MemoryExtractor

    session_mgr = SessionManager()
    session_mgr.load_state()

    if no_extract:
        extraction = {}
        if summary:
            session_mgr._write_handoff({}, summary)
            console.print("[green]✓ Handoff written (extraction skipped)[/green]")
        else:
            console.print("[yellow]--summary or extraction required[/yellow]")
        return

    agent = Agent()
    extractor = MemoryExtractor(agent)

    with console.status("[bold green]Extracting memory..."):
        recent = agent.memory.recent(n=30)
        entries = [
            {"prompt": r["prompt"], "response": r["response"], "timestamp": r["timestamp"]}
            for r in recent
        ]
        extraction = extractor.extract_from_session(entries)

    session_mgr.end_session(extraction, summary)

    console.print("\n[bold green]✓ Session memory saved[/bold green]")

    if extraction.get("tasks_completed"):
        console.print(f"  Completed: {len(extraction['tasks_completed'])} task(s)")
    if extraction.get("tasks_in_progress"):
        console.print(f"  In progress: {len(extraction['tasks_in_progress'])} task(s)")
    if extraction.get("next_session_start"):
        console.print(f"  Resume point: {extraction['next_session_start'][:80]}")
    console.print("\n[dim]Next session: cairn memory boot[/dim]")


@memory_app.command("boot")
def memory_boot(
    fmt: str = typer.Option("text", "--format", "-f", help="text | claude | json"),
    copy: bool = typer.Option(False, "--copy", "-c", help="Copy to clipboard"),
    memory: list[str] = typer.Option([], "--memory", "-m", help="Extra memory file"),
):
    """Start of session: generate boot context."""
    from cairn.core import Project
    from cairn.memory.injector import ContextInjector

    injector = ContextInjector()
    decisions = Project(load_session=False).memory.active_decisions()
    context = injector.build(fmt=fmt, include_memory=memory or None, decisions=decisions)
    token_est = injector.estimate_tokens(fmt)

    if copy:
        try:
            import pyperclip
            pyperclip.copy(context)
            console.print(f"[green]✓ Copied to clipboard (~{token_est} tokens)[/green]")
        except ImportError:
            console.print("[yellow]pyperclip not installed: pip install pyperclip[/yellow]")
            console.print(context)
    else:
        console.print(context)
        console.print(f"\n[dim]~{token_est} tokens | use cairn memory boot --copy to copy to clipboard[/dim]")


@memory_app.command("status")
def memory_status():
    """Show current session status."""
    from cairn.memory.session import SessionManager
    from cairn.memory.manifest import ManifestManager

    session_mgr = SessionManager()
    manifest_mgr = ManifestManager()
    state = session_mgr.load_state()

    table = Table(title="Session status", box=box.ROUNDED)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Project", state.get("project", Path.cwd().name))
    table.add_row("Active task", state.get("current_task") or "[dim]-[/dim]")
    table.add_row("State", state.get("task_status", "idle"))
    table.add_row("Session #", str(state.get("session_count", 0)))
    table.add_row("Last updated", (state.get("updated_at") or "-")[:19])
    table.add_row("Manifest", "[green]✓[/green]" if manifest_mgr.exists() else "[red]✗ missing[/red]")

    console.print(table)

    from cairn.config.loader import load_config
    from cairn.memory.memory import LEGACY_SHARED_DB, resolve_db_path
    db_path = resolve_db_path(load_config().get("memory", {}))
    console.print(f"[dim]Project DB: {db_path}[/dim]")
    legacy = Path(LEGACY_SHARED_DB).expanduser()
    if legacy.exists() and legacy != db_path:
        console.print(
            f"[yellow]Note: {legacy} holds data from before per-project DBs. "
            "It is no longer read for this project and is left untouched.[/yellow]"
        )

    files = session_mgr.list_memory_files()
    if files:
        console.print(f"\n[dim]Memory files: {', '.join(files)}[/dim]")

    handoff = session_mgr.load_handoff()
    if handoff:
        lines = [ln for ln in handoff.split("\n") if ln.strip()][:5]
        console.print(Panel("\n".join(lines), title="Last handoff", border_style="dim"))


@memory_app.command("manifest")
def memory_manifest(
    regen: bool = typer.Option(False, "--regen", help="Regenerate the manifest"),
):
    """Create or show context-manifest.md."""
    from cairn.memory.manifest import ManifestManager

    manifest_mgr = ManifestManager()

    if not regen and manifest_mgr.exists():
        console.print(manifest_mgr.load())
        return

    from cairn.core import Agent
    agent = Agent()

    with console.status("[bold green]Generating manifest..."):
        content = manifest_mgr.generate(agent)

    console.print("[green]✓ context-manifest.md generated[/green]")
    console.print(content)


@memory_app.command("search")
def memory_search(
    query: str = typer.Argument(..., help="Search term"),
    limit: int = typer.Option(5, "--limit", "-n"),
    path: str = typer.Option(
        "", "--path", "-p",
        help="Scope to a workspace-relative path (e.g. 'apps/api'); repo-wide entries still match",
    ),
):
    """Search memory (SQLite + files)."""
    from cairn.core import Agent
    from cairn.memory.session import SessionManager

    agent = Agent()
    session_mgr = SessionManager()

    results_found = False

    db_results = agent.memory.search(query, limit=limit, path=path or None)
    if db_results:
        results_found = True
        table = Table(title=f"SQLite: '{query}'", box=box.ROUNDED)
        table.add_column("Time", style="dim", width=20)
        table.add_column("Scope", style="magenta", width=7)
        table.add_column("Prompt", style="cyan")
        table.add_column("Response", style="white")
        for r in db_results:
            table.add_row(r["timestamp"][:19], r.get("scope", "project"), r["prompt"][:60], r["response"][:80])
        console.print(table)

    for fname in session_mgr.list_memory_files():
        content = session_mgr.load_memory_file(fname)
        if query.lower() in content.lower():
            results_found = True
            lines = [ln for ln in content.split("\n") if query.lower() in ln.lower()]
            console.print(f"\n[cyan]{fname}:[/cyan]")
            for line in lines[:5]:
                console.print(f"  {line.strip()}")

    if not results_found:
        console.print(f"[yellow]No results found for '{query}'.[/yellow]")


@memory_app.command("check")
def memory_check(
    approach: str = typer.Argument(..., help="The approach being considered"),
    limit: int = typer.Option(5, "--limit", "-n"),
):
    """Check whether an approach was already rejected or abandoned."""
    from cairn.core import Agent

    agent = Agent()
    rows = agent.memory.check_rejected(approach, limit=limit)

    if not rows:
        console.print("[green]No record of this being tried before.[/green]")
        return

    table = Table(title=f"Prior history: '{approach}'", box=box.ROUNDED)
    table.add_column("Scope", style="magenta", width=7)
    table.add_column("Kind", style="yellow")
    table.add_column("Decision #", style="cyan")
    table.add_column("Reason", style="white")
    for r in rows:
        kind = "rejected alt" if r["kind"] == "rejected_alternative" else "abandoned"
        table.add_row(r["scope"], kind, str(r["decision_id"]), r["reason"] or "[dim]-[/dim]")
    console.print(table)


@memory_app.command("recent")
def memory_recent(
    n: int = typer.Option(10, "--n", "-n", help="How many recent entries"),
):
    """Show the most recent memory entries."""
    from cairn.core import Agent

    agent = Agent()
    results = agent.memory.recent(n=n)

    if not results:
        console.print("[yellow]Memory is empty.[/yellow]")
        return

    table = Table(title="Recent memory entries", box=box.SIMPLE)
    table.add_column("Time", style="dim", width=20)
    table.add_column("Prompt", style="cyan")
    for r in results:
        table.add_row(r["timestamp"][:19], r["prompt"][:80])
    console.print(table)


@memory_app.command("invalidate")
def memory_invalidate(
    mem_id: str = typer.Argument(..., help="ID of the record to invalidate ('g<n>' for a global entry)"),
    reason: str = typer.Option("", "--reason", "-r", help="Reason (optional)"),
):
    """Invalidate a memory record (does not delete; sets valid_until)."""
    from cairn.core import Agent

    agent = Agent()
    ok = agent.memory.invalidate(mem_id, reason=reason)
    if ok:
        console.print(f"[green]✓ Record #{mem_id} invalidated.[/green]")
        if reason:
            console.print(f"  [dim]Reason: {reason}[/dim]")
    else:
        console.print(
            f"[yellow]Record #{mem_id} not found or already invalidated.[/yellow]"
        )


@memory_app.command("dashboard")
def memory_dashboard(
    open_browser: bool = typer.Option(False, "--open", help="Open in default browser after writing"),
    filt: str = typer.Option("all", "--filter", help="all | active | invalidated | recent"),
    fmt: str = typer.Option("html", "--format", help="html | json"),
):
    """Generate the project decision dashboard (single-file HTML)."""
    from cairn.interface.commands.dashboard import dashboard_command
    dashboard_command(open_browser=open_browser, filt=filt, fmt=fmt)


@memory_app.command("clear")
def memory_clear(
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Clear all SQLite memory."""
    if not confirm:
        typer.confirm("SQLite memory will be deleted. Are you sure?", abort=True)

    from cairn.core import Agent
    agent = Agent()
    count = agent.memory.clear()
    console.print(f"[green]✓ {count} entries deleted.[/green]")


@memory_app.command("export")
def memory_export():
    """Sync decisions with .cairn/decisions/*.md (one committed file per decision)."""
    from cairn.core import Project
    project = Project(load_session=False)
    counts = project.memory.sync_decision_files()
    console.print(f"[green]Decision files synced:[/green] {counts}")


@memory_app.command("for-path")
def memory_for_path(
    path: str = typer.Argument("", help="File or directory (omit with --hook)"),
    hook: bool = typer.Option(
        False, "--hook",
        help="Claude Code PreToolUse hook mode: read the hook JSON on stdin, print hook JSON",
    ),
):
    """Show the active decisions that apply to a file."""
    import json
    import sys
    from cairn.core import Project
    from cairn.memory.decision_files import format_brief

    project_dir = Path.cwd()
    if hook:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except json.JSONDecodeError:
            return
        path = ((payload.get("tool_input") or {}).get("file_path")) or ""
        project_dir = Path(payload.get("cwd") or project_dir)
        if not path or not (project_dir / ".cairn").is_dir():
            return
    project = Project(project_dir=project_dir, load_session=False)
    rows = project.memory.decisions_for_path(path)
    text = format_brief(rows, f"Cairn decisions that apply to {path}:")
    if hook:
        if text:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": text + "\nFollow these, or ask the user before going against one.",
            }}))
        return
    console.print(text or f"[dim]No path-scoped decisions for {path}.[/dim]", markup=False)


@memory_app.command("nudge")
def memory_nudge():
    """Claude Code Stop hook (reads hook JSON on stdin): remind the agent to
    record decisions after many edits with none recorded. Silent otherwise."""
    import json
    import sys
    from cairn.config.loader import load_config
    from cairn.memory.capture import nudge_message

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return
    project_dir = Path(payload.get("cwd") or Path.cwd())
    cairn_dir = project_dir / ".cairn"
    if not cairn_dir.is_dir():
        return
    config = load_config(cairn_dir / "config.yaml" if (cairn_dir / "config.yaml").exists() else None)
    threshold = int(((config.get("memory") or {}).get("nudge") or {}).get("edits", 8))
    msg = nudge_message(payload, cairn_dir / "persistence" / "nudge-state.json", threshold)
    if msg:
        # Exit code 2 + stderr: Claude Code shows stderr to the model and keeps going.
        print(msg, file=sys.stderr)
        raise typer.Exit(code=2)


@memory_app.command("capture")
def memory_capture(
    rev_range: str = typer.Option("HEAD~20..HEAD", "--from-git", help="git revision range to read"),
):
    """Propose decisions from git history with the helper model; you confirm each."""
    import subprocess
    from cairn.core import Project
    from cairn.memory.capture import propose_from_git

    project = Project(load_session=False)
    log = subprocess.run(
        ["git", "log", "--stat", "--format=commit %h%n%s%n%n%b", rev_range],
        cwd=project.project_dir, capture_output=True, text=True, encoding="utf-8", errors="ignore",
    )
    if log.returncode != 0:
        console.print(f"[red]git log failed: {log.stderr.strip()}[/red]", markup=False)
        raise typer.Exit(code=1)
    if not log.stdout.strip():
        console.print("[yellow]No commits in that range.[/yellow]")
        return
    with console.status("[bold green]Reading commits..."):
        proposals = propose_from_git(log.stdout, project.helper_call)
    if proposals is None:
        console.print(
            "[yellow]No helper model available (memory_helper_model unset or unreachable). "
            "Nothing was recorded.[/yellow]"
        )
        raise typer.Exit(code=1)
    if not proposals:
        console.print("[dim]No design decisions found in that range.[/dim]")
        return
    recorded = 0
    for p in proposals:
        console.print(Panel(
            f"{p['decision']}\n\n[dim]why:[/dim] {p.get('reasoning') or '(none given)'}"
            f"\n[dim]path:[/dim] {p.get('scope_path') or 'repo-wide'}"
            f"   [dim]commit:[/dim] {p.get('commit') or '?'}",
            title="Proposed decision", border_style="cyan",
        ))
        if typer.confirm("Record it?", default=False):
            evidence = [{"source": f"commit {p['commit']}", "claim": p["decision"]}] if p.get("commit") else None
            project.memory.record_decision(
                p["decision"],
                reasoning=p.get("reasoning") or None,
                alternatives=[a for a in (p.get("alternatives") or []) if isinstance(a, dict)] or None,
                evidence=evidence,
                scope_path=p.get("scope_path") or None,
            )
            recorded += 1
    console.print(f"[green]Recorded {recorded} of {len(proposals)} proposals.[/green]")


@memory_app.command("stale")
def memory_stale():
    """List decisions whose code has changed a lot since they were recorded."""
    from cairn.core import Project
    project = Project(load_session=False)
    rows = project.memory.stale_decisions()
    if not rows:
        console.print("[green]No decisions need review.[/green]")
        return
    table = Table(title="Decisions that need review", box=box.ROUNDED)
    table.add_column("ID", style="cyan")
    table.add_column("Path", style="magenta")
    table.add_column("Commits since", justify="right")
    table.add_column("Decision", style="white")
    for r in rows:
        table.add_row(str(r["id"]), r["path"], str(r["commits_since"]), r["decision"][:70])
    console.print(table)
    console.print(
        "[dim]Still right? cairn memory confirm <id>. "
        "Changed? record a new decision with supersedes=<id>.[/dim]"
    )


@memory_app.command("confirm")
def memory_confirm(mem_id: str = typer.Argument(..., help="Decision id")):
    """Confirm a decision still holds (resets its staleness counter)."""
    from cairn.core import Project
    project = Project(load_session=False)
    try:
        ok = project.memory.confirm_decision(mem_id)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    console.print("[green]Confirmed.[/green]" if ok else f"[yellow]No decision #{mem_id}.[/yellow]")


@memory_app.command("import-adr")
def memory_import_adr(
    adr_dir: Path = typer.Argument(..., help="Folder of ADR markdown files, e.g. docs/adr"),
):
    """Import existing ADRs (Nygard or MADR) as Cairn decisions."""
    from cairn.core import Project
    if not adr_dir.is_dir():
        console.print(f"[red]Not a folder: {adr_dir}[/red]")
        raise typer.Exit(code=1)
    project = Project(load_session=False)
    try:
        counts = project.memory.import_adrs(adr_dir)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]ADR import:[/green] {counts}")


@memory_app.command("reindex")
def memory_reindex():
    """Rebuild the decision index in SQLite from .cairn/decisions/*.md."""
    from cairn.core import Project
    project = Project(load_session=False)
    try:
        counts = project.memory.reindex_decisions()
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Reindexed from files:[/green] {counts}")
