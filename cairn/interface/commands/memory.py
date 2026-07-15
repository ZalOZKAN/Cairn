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
    from cairn.memory.injector import ContextInjector

    injector = ContextInjector()
    context = injector.build(fmt=fmt, include_memory=memory or None)
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
        table.add_column("Prompt", style="cyan")
        table.add_column("Response", style="white")
        for r in db_results:
            table.add_row(r["timestamp"][:19], r["prompt"][:60], r["response"][:80])
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
    mem_id: int = typer.Argument(..., help="ID of the record to invalidate"),
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
    """Export all decisions to .cairn/persistence/memory/decisions.md."""
    from cairn.core import Agent
    agent = Agent()
    agent.memory._export_decisions_to_markdown()
    console.print("[green]✓ Decisions exported to .cairn/persistence/memory/decisions.md[/green]")
