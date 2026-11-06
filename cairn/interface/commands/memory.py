"""cairn memory: session memory management."""
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
            console.print("[green]Handoff written (extraction skipped).[/green]")
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

    console.print("\n[bold green]Session memory saved.[/bold green]")

    if extraction.get("tasks_completed"):
        console.print(f"  Completed: {len(extraction['tasks_completed'])} task(s)")
    if extraction.get("tasks_in_progress"):
        console.print(f"  In progress: {len(extraction['tasks_in_progress'])} task(s)")
    if extraction.get("next_session_start"):
        console.print(f"  Resume point: {extraction['next_session_start'][:80]}")
    console.print("\n[dim]Next session: cairn memory context[/dim]")


@memory_app.command("context")
def memory_context(
    fmt: str = typer.Option("text", "--format", "-f", help="text | claude | json"),
    copy: bool = typer.Option(False, "--copy", "-c", help="Copy to clipboard"),
    memory: list[str] = typer.Option([], "--memory", "-m", help="Extra memory file"),
):
    """Start of session: print the context to give an agent."""
    from cairn.core import Project
    from cairn.memory.injector import ContextInjector

    from cairn.memory.notices import boot_notice

    injector = ContextInjector()
    mem = Project(load_session=False).memory
    decisions = mem.decisions_block()
    notice = boot_notice(mem)
    if notice:
        decisions = notice + "\n\n" + decisions
    from cairn.memory import inbox as _inbox
    waiting = _inbox.summary(Path.cwd() / ".cairn")
    if waiting["pending"]:
        decisions = (f"Cairn inbox: {waiting['pending']} proposed decision(s) wait for confirmation "
                     f"({waiting['ready']} seen several times). Review with `cairn memory inbox`. "
                     "They are not decisions yet.\n\n") + decisions
    context = injector.build(fmt=fmt, include_memory=memory or None, decisions=decisions)

    if copy:
        try:
            import pyperclip
            pyperclip.copy(context)
            console.print("[green]Copied to clipboard.[/green]")
        except ImportError:
            console.print("[yellow]pyperclip not installed: pip install pyperclip[/yellow]")
            console.print(context)
    else:
        console.print(context)
        console.print("\n[dim]Use cairn memory context --copy to copy to clipboard[/dim]")


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
    table.add_row("Manifest", "[green]OK[/green]" if manifest_mgr.exists() else "[red]missing[/red]")

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

    console.print("[green]context-manifest.md generated.[/green]")
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
        console.print(f"[green]Record #{mem_id} invalidated.[/green]")
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
    console.print(f"[green]{count} entries deleted.[/green]")


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
    full: bool = typer.Option(
        False, "--full",
        help="Each decision with its reasoning and rejected alternatives (the AGENTS.md block's "
             "format) instead of one line",
    ),
):
    """Show the active decisions that apply to a file."""
    import json
    import sys
    from cairn.core import Project
    from cairn.memory.decision_files import format_brief
    from cairn.memory.stats import bump

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
    if hook:
        _block_if_forbidden(project.memory, payload, project_dir)
    rows = project.memory.decisions_for_path(path)
    if hook and payload.get("session_id"):
        rows = _unseen_this_session(project_dir, payload["session_id"], rows)
    header = f"Cairn decisions that apply to {path}:"
    text = _full_entries(project.memory, rows, header) if full else format_brief(rows, header)
    lesson_text = _lessons_text(project_dir, path)
    if lesson_text:
        text = (text + "\n" if text else "") + lesson_text
    if hook:
        bump(project_dir, "for_path_hook", hit=bool(text))
        if text:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": text + "\nFollow these, or ask the user before going against one.",
            }}))
        return
    console.print(text or f"No path-scoped decisions for {path}.", markup=False)


@memory_app.command("for-diff")
def memory_for_diff(
    spec: str = typer.Argument("", help="Git revision or range, e.g. main..HEAD (default: all uncommitted work)"),
    staged: bool = typer.Option(False, "--staged", help="Only what is staged"),
):
    """Show the active decisions that apply to the files a diff changes."""
    from cairn.core import Project
    from cairn.memory.decision_files import format_brief
    from cairn.memory.diff_scope import changed_paths, decisions_for_diff

    if spec and staged:
        console.print("[red]Give a revision range or --staged, not both.[/red]")
        raise typer.Exit(code=2)
    project = Project(load_session=False)
    paths = changed_paths(project.project_dir, spec, staged)
    if paths is None:
        console.print(f"[red]Could not read the diff{f' for {spec}' if spec else ''}: "
                      "not a git repository, or an unknown revision.[/red]")
        raise typer.Exit(code=1)
    what = spec or ("the staged changes" if staged else "your uncommitted changes")
    rows = decisions_for_diff(project.memory, paths)
    text = format_brief(rows, f"Cairn decisions that apply to {what} ({len(paths)} changed files):")
    console.print(text or f"No path-scoped decisions apply to {what} ({len(paths)} changed files).",
                  markup=False, soft_wrap=True)


def _full_entries(memory, rows: list[dict], header: str) -> str:
    """`rows` as the AGENTS.md block shows them: decision, why, rejected
    alternatives, forbidden patterns, plus the staleness flag."""
    from cairn.memory import agents_md
    if not rows:
        return ""
    entries = agents_md.entries_by_id(memory)
    lines = [header]
    for r in rows:
        lines += entries.get(r["id"], [f"- **{r['decision']}**"])
        if r.get("stale"):
            lines.append(f"  - NEEDS REVIEW: {r['stale']['commits_since']} commits since recorded")
    return "\n".join(lines)


def _block_if_forbidden(memory, payload: dict, project_dir: Path) -> None:
    """Exit 2 with the reason on stderr when the edit in a PreToolUse /
    BeforeTool payload adds a line a decision forbids: both Claude Code and
    Gemini CLI treat exit code 2 from such a hook as "block this tool call and
    show stderr to the model"."""
    import sys
    from cairn.memory import enforce
    from cairn.memory.stats import bump
    edit = enforce.edit_from_hook(payload, project_dir)
    found = enforce.violations(memory, *edit) if edit else None
    if found is None:
        return
    bump(project_dir, "guard", hit=bool(found))
    if found:
        print(enforce.block_message(found), file=sys.stderr)
        raise typer.Exit(code=2)


@memory_app.command("guard")
def memory_guard(
    spec: str = typer.Argument("", help="Git revision or range, e.g. main..HEAD (default: all uncommitted work)"),
    staged: bool = typer.Option(False, "--staged", help="Only what is staged (for a git pre-commit hook)"),
    hook: bool = typer.Option(
        False, "--hook",
        help="Claude Code PreToolUse / Gemini CLI BeforeTool hook mode: read the hook JSON on stdin "
             "and block (exit 2) an edit whose added lines match a decision's forbid pattern",
    ),
):
    """Fail when added code matches a decision's `forbid` pattern (only the lines a change adds)."""
    import json
    import sys
    from cairn.core import Project
    from cairn.memory import enforce

    if hook:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except json.JSONDecodeError:
            return
        project_dir = Path(payload.get("cwd") or Path.cwd())
        if (project_dir / ".cairn").is_dir():
            _block_if_forbidden(Project(project_dir=project_dir, load_session=False).memory, payload, project_dir)
        return
    if spec and staged:
        console.print("[red]Give a revision range or --staged, not both.[/red]")
        raise typer.Exit(code=2)
    project = Project(load_session=False)
    added = enforce.diff_added_lines(project.project_dir, spec, staged)
    if added is None:
        console.print(f"[red]Could not read the diff{f' for {spec}' if spec else ''}: "
                      "not a git repository, or an unknown revision.[/red]")
        raise typer.Exit(code=1)
    found = [v for path, lines in added.items() for v in (enforce.violations(project.memory, path, lines) or [])]
    if not found:
        console.print(f"No forbidden patterns in the lines added ({len(added)} changed files).", markup=False)
        return
    console.print(enforce.block_message(found, "Cairn: these changes add code that a recorded decision forbids."),
                  markup=False, soft_wrap=True)
    raise typer.Exit(code=1)


def _lessons_text(project_dir: Path, path: str) -> str:
    """The lessons that name this file, as lines for an agent; empty when there are none."""
    from cairn.memory import lessons

    try:
        rel = Path(path).resolve().relative_to(project_dir.resolve()).as_posix()
    except (ValueError, OSError):
        rel = path
    found = lessons.for_path(project_dir / ".cairn", rel)
    if not found:
        return ""
    return f"Lessons for {rel}:\n" + "\n".join(f"- {i['text']} ({i['kind']}, confidence {lessons.confidence_now(i)})" for i in found[:5])


def _unseen_this_session(project_dir: Path, session_id: str, rows: list[dict]) -> list[dict]:
    """Keep only decisions not yet shown in this session. Claude Code fires
    PreToolUse before every Edit, so without this the same reminder is
    re-injected on each edit (measured: the main source of Cairn's extra
    turns). State is per session and capped to the most recent sessions."""
    import json
    state_file = project_dir / ".cairn" / "persistence" / "hook-seen.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    seen = set(state.get(session_id, []))
    fresh = [r for r in rows if r["uid"] not in seen]
    if fresh:
        state.pop(session_id, None)
        state[session_id] = sorted(seen | {r["uid"] for r in fresh})
        state = dict(list(state.items())[-20:])
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state), encoding="utf-8")
    return fresh


def _catch_correction(project_dir: Path, payload: dict) -> None:
    """A message like "no, use X instead of Y" becomes a pending proposal (with the message as evidence). Never fails the hook."""
    from cairn.memory import inbox
    from cairn.memory.corrections import detect_correction

    try:
        found = detect_correction(str(payload.get("prompt") or ""))
        if found:
            inbox.add(project_dir / ".cairn", text=found["text"], evidence=found["evidence"], source="correction",
                      rejected=found["rejected"])
    except Exception:
        return


@memory_app.command("detect")
def memory_detect():
    """Claude Code UserPromptSubmit / Gemini CLI BeforeAgent hook (reads hook
    JSON on stdin): if the user's message reads like a decision stated in
    passing, remind the agent to record it. Silent otherwise; no LLM call."""
    import json
    import sys
    from cairn.memory.capture import decision_language_reminder

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return
    project_dir = Path(payload.get("cwd") or Path.cwd())
    if not (project_dir / ".cairn").is_dir():
        return
    _catch_correction(project_dir, payload)
    text = decision_language_reminder(payload)
    if text:
        # Echo the host's own event name (Claude Code: UserPromptSubmit,
        # Gemini CLI: BeforeAgent); both put additionalContext in front of
        # the model this turn (verified live for Gemini CLI 0.61.0).
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": payload.get("hook_event_name") or "UserPromptSubmit",
            "additionalContext": text,
        }}))


@memory_app.command("probe")
def memory_probe(
    host: str = typer.Option("unknown", "--host", help="Name of the tool running this hook"),
    show: bool = typer.Option(False, "--report", help="Summarize what the probe has seen, per host"),
):
    """Hook probe: record only the structure of what a host's hook sends (never message text)."""
    import json
    import sys
    from cairn.memory import probe

    if show:
        rows = probe.report(Path.cwd())
        if not rows:
            console.print("[yellow]No probe data yet.[/yellow]")
            return
        table = Table(title="Host probe", box=box.ROUNDED)
        for col in ("Host", "Probes", "Events", "Prompt", "Transcript", "Assistant text", "Record types"):
            table.add_column(col)
        for r in rows:
            table.add_row(r["host"], str(r["probes"]), ", ".join(r["events"]), f"{r['prompt_rate']:.0%}",
                          f"{r['transcript_rate']:.0%}", f"{r['extraction_rate']:.0%}",
                          ", ".join(f"{k}:{v}" for k, v in r["record_types"].items()) or "-")
        console.print(table)
        console.print("Payload keys seen: " + "; ".join(f"{r['host']}: {', '.join(r['keys'])}" for r in rows))
        return
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if isinstance(payload, dict):
            probe.record(Path(payload.get("cwd") or Path.cwd()), payload, host)
    except Exception:
        pass


@memory_app.command("nudge")
def memory_nudge():
    """Claude Code Stop hook (reads hook JSON on stdin): remind the agent to
    record decisions after many edits with none recorded. Silent otherwise.
    Gemini CLI's AfterAgent is documented to retry on the same exit code 2,
    unverified, so Cairn does not register it there."""
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
        # Exit code 2 + stderr: Claude Code shows stderr to the model and keeps
        # going; Gemini CLI's AfterAgent rejects the response and retries with
        # stderr as the new prompt.
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
        try:
            answer = typer.confirm("Record it?", default=False)
        except typer.Abort:
            # A closed/absent stdin raises here rather than returning the
            # default; nothing left to ask, so stop instead of raising once
            # per remaining proposal.
            break
        if answer:
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


@memory_app.command("inbox")
def memory_inbox(
    confirm_id: str = typer.Option("", "--confirm", help="Record this proposal as a decision"),
    dismiss_id: str = typer.Option("", "--dismiss", help="Drop this proposal; it will not be proposed again"),
):
    """Proposed decisions waiting for confirmation. Nothing here is read by agents until it is confirmed."""
    from cairn.core import Project
    from cairn.memory import inbox

    project = Project(load_session=False)
    cairn_dir = project.project_dir / ".cairn"
    inbox.purge(cairn_dir)
    if confirm_id:
        try:
            console.print(f"Recorded as decision {inbox.confirm(cairn_dir, confirm_id, project.memory, recorded_by='cli')}.", markup=False)
        except KeyError:
            console.print("No such pending proposal.", markup=False)
            raise typer.Exit(code=1)
        return
    if dismiss_id:
        if not inbox.dismiss(cairn_dir, dismiss_id):
            console.print("No such pending proposal.", markup=False)
            raise typer.Exit(code=1)
        console.print("Dropped.", markup=False)
        return
    rows = inbox.items(cairn_dir)
    if not rows:
        console.print("The inbox is empty.", markup=False)
        return
    for r in rows:
        rejected = f"\n  rejected: {', '.join(r['rejected'])}" if r.get("rejected") else ""
        console.print(f"{r['id']}  (seen {r.get('seen', 1)}x, {r.get('source')})\n  {r['text']}\n  why: {r.get('why') or '-'}\n"
                      f"  evidence: {r.get('evidence') or '-'}{rejected}", markup=False)


@memory_app.command("learn")
def memory_learn(
    hook: bool = typer.Option(False, "--hook", help="Claude Code Stop or PreCompact hook: read the hook JSON on stdin; silent"),
    transcript: str = typer.Option("", "--transcript", help="A transcript file to learn from (without --hook)"),
):
    """Let the helper model read the new part of a session and put the decisions it finds in the inbox (never straight into the
    decisions). Needs a helper model; without one nothing happens."""
    import json
    import sys
    from cairn.core import Project
    from cairn.memory.learn import learn

    session_id, project_dir, path = "manual", Path.cwd(), Path(transcript) if transcript else None
    source = "stop"
    if hook:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except json.JSONDecodeError:
            return
        project_dir = Path(payload.get("cwd") or project_dir)
        path = Path(payload.get("transcript_path") or "")
        session_id = str(payload.get("session_id") or "hook")
        source = "compact" if payload.get("hook_event_name") == "PreCompact" else "stop"
        if not (project_dir / ".cairn").is_dir():
            return
    if not path or not path.is_file():
        if not hook:
            console.print("Give a transcript file with --transcript, or use --hook.", markup=False)
            raise typer.Exit(code=1)
        return
    try:
        project = Project(project_dir=project_dir, load_session=False)
        result = learn(project_dir / ".cairn", path, session_id, project.helper_call, source=source)
    except Exception:
        if hook:
            return                                       # a hook must never fail a session
        raise
    if hook:
        return
    if not result["available"]:
        console.print("No helper model available (memory_helper_model unset or unreachable). Nothing was read.", markup=False)
        raise typer.Exit(code=1)
    console.print(f"Added {result['added']} proposal(s), {result['repeats']} seen again. Review with: cairn memory inbox", markup=False)


@memory_app.command("lesson")
def memory_lesson(
    text: str = typer.Argument(..., help="What was learned, in one sentence"),
    files: list[str] = typer.Option(..., "--file", "-f", help="A file or folder it concerns (repeat for more)"),
    kind: str = typer.Option("pitfall", "--type", help="pitfall | pattern | preference"),
    origin: str = typer.Option("observed", "--origin", help="user-stated | observed | inferred"),
    confidence: int = typer.Option(6, "--confidence", help="1 to 10"),
):
    """Store a lesson about particular files; it is shown only when one of them is touched."""
    from cairn.memory import lessons

    try:
        item = lessons.record(Path.cwd() / ".cairn", text, files, kind=kind, origin=origin, confidence=confidence)
    except ValueError as exc:
        console.print(f"Not stored: {exc}", markup=False)
        raise typer.Exit(code=1)
    console.print(f"Lesson {item['id']} stored (seen {item['seen']}x).", markup=False)


@memory_app.command("lessons")
def memory_lessons(
    path: str = typer.Argument("", help="Only the lessons for this file or folder"),
    forget: str = typer.Option("", "--forget", help="Delete the lesson with this id"),
    stale: bool = typer.Option(False, "--stale", help="List lessons none of whose files exist any more"),
):
    """List, find stale or delete lessons."""
    from cairn.memory import lessons

    cairn_dir = Path.cwd() / ".cairn"
    if forget:
        if not lessons.forget(cairn_dir, forget):
            console.print("No such lesson.", markup=False)
            raise typer.Exit(code=1)
        console.print("Deleted.", markup=False)
        return
    rows = lessons.stale(cairn_dir, Path.cwd()) if stale else (lessons.for_path(cairn_dir, path) if path else lessons._all(cairn_dir))
    if not rows:
        console.print("No lessons.", markup=False)
        return
    for r in rows:
        console.print(f"{r['id']}  {r['kind']}, {r['origin']}, confidence {lessons.confidence_now(r)}, seen {r.get('seen', 1)}x\n"
                      f"  {r['text']}\n  files: {', '.join(r['files'])}", markup=False)


@memory_app.command("stats")
def memory_stats():
    """Local usage counters: how often the agent used each Cairn tool and each hook, and hit rates."""
    from cairn.memory.stats import load
    data = load(Path.cwd())
    tools = data.get("tools") or {}
    decisions_dir = Path.cwd() / ".cairn" / "decisions"
    n_files = len(list(decisions_dir.glob("*.md"))) if decisions_dir.is_dir() else 0
    console.print(f"Decision files: {n_files}")
    if not tools:
        console.print("[dim]No tool or hook calls recorded yet.[/dim]")
        return
    table = Table(title=f"Tool usage since {str(data.get('since', '?'))[:10]}", box=box.ROUNDED)
    table.add_column("Tool", style="cyan")
    table.add_column("Calls", justify="right")
    table.add_column("Returned something", justify="right")
    for name, e in sorted(tools.items(), key=lambda kv: -kv[1]["calls"]):
        rate = f"{e['hits'] / e['calls']:.0%}" if e["calls"] else "-"
        table.add_row(name, str(e["calls"]), f"{e['hits']} ({rate})")
    console.print(table)


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


@memory_app.command("review")
def memory_review():
    """List decisions held as possible prompt injection, for a human to approve or leave."""
    from cairn.core import Project
    from cairn.interface.i18n import current_language, t
    project = Project(load_session=False)
    lang = current_language(project.config)
    rows = project.memory.quarantined_decisions()
    if not rows:
        console.print(f"[green]{t('review.none', lang)}[/green]")
        return
    table = Table(title=t("review.title", lang), box=box.ROUNDED)
    for key in ("col.id", "col.decision", "col.reason", "col.source"):
        table.add_column(t(key, lang))
    for r in rows:
        table.add_row(r["uid"] or str(r["id"]), r["decision"][:90], r["reason"], r["recorded_by"] or "-")
    console.print(table)
    console.print(f"[dim]{t('review.hint', lang)}[/dim]")


@memory_app.command("approve")
def memory_approve(mem_id: str = typer.Argument(..., help="Decision id or uid from `cairn memory review`")):
    """Release a quarantined decision. Only a person can do this: no MCP tool approves."""
    from cairn.core import Project
    from cairn.interface.i18n import current_language, t
    project = Project(load_session=False)
    lang = current_language(project.config)
    try:
        ok = project.memory.approve_quarantined(mem_id)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    if not ok:
        console.print(f"[yellow]{t('approve.missing', lang, id=mem_id)}[/yellow]")
        raise typer.Exit(code=1)
    console.print(f"[green]{t('approve.ok', lang)}[/green]")


@memory_app.command("missed")
def memory_missed(
    days: int = typer.Option(30, "--days", help="Only sessions from the last N days"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Messages that read like a decision but were never recorded, from Claude Code transcripts."""
    import json
    from cairn.config.loader import load_config
    from cairn.interface.i18n import current_language, t
    from cairn.memory.usage import project_report
    lang = current_language(load_config())
    report = project_report(Path.cwd(), days=days)
    if as_json:
        typer.echo(json.dumps({k: report[k] for k in ("signals", "missed", "capture_rate")} | {
            "items": [m | {"session": s["session"]} for s in report["sessions"] for m in s["missed"]]},
            ensure_ascii=False, indent=2))
        return
    rate = f"{report['capture_rate']:.0%}" if report["capture_rate"] is not None else "-"
    console.print(t("missed.summary", lang, signals=report["signals"], covered=report["signals"] - report["missed"],
                    missed=report["missed"], rate=rate))
    if not report["missed"]:
        console.print(f"[green]{t('missed.none', lang)}[/green]")
        return
    table = Table(title=t("missed.title", lang), box=box.ROUNDED)
    for key in ("col.when", "col.signal", "col.message"):
        table.add_column(t(key, lang))
    for s in report["sessions"]:
        for m in s["missed"]:
            table.add_row(str(m["at"] or "")[:16].replace("T", " "), m["signal"], m["quote"])
    console.print(table)
    console.print(f"[dim]{t('missed.hint', lang)}[/dim]")


@memory_app.command("usage")
def memory_usage(
    days: int = typer.Option(30, "--days", help="Only sessions from the last N days"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Token use per session with and without Cairn, as recorded in Claude Code transcripts."""
    import json
    from cairn.config.loader import load_config
    from cairn.interface.i18n import current_language, t
    from cairn.memory.usage import billable_input, project_report
    lang = current_language(load_config())
    report = project_report(Path.cwd(), days=days)
    if as_json:
        typer.echo(json.dumps({"groups": report["groups"],
                               "capture_rate": report["capture_rate"]}, ensure_ascii=False, indent=2))
        return
    if not report["sessions"]:
        console.print(f"[yellow]{t('usage.none', lang)}[/yellow]")
    else:
        table = Table(title=t("usage.title", lang), box=box.ROUNDED)
        for key in ("col.group", "col.count", "col.calls", "col.input", "col.cache_read", "col.output",
                    "col.billable"):
            table.add_column(t(key, lang), justify="left" if key == "col.group" else "right")
        for key, label in (("with_cairn", "group.with"), ("without_cairn", "group.without")):
            g = report["groups"][key]
            avg = g["avg_tokens"]
            table.add_row(t(label, lang), str(g["sessions"]), str(g["avg_model_calls"]),
                          f"{avg.get('input', 0):,}", f"{avg.get('cache_read', 0):,}", f"{avg.get('output', 0):,}",
                          f"{billable_input(avg):,.0f}" if avg else "-")
        console.print(table)
        console.print(f"[dim]{t('usage.billable_note', lang)}[/dim]")
        console.print(f"[dim]{t('usage.caveat', lang)}[/dim]")


@memory_app.command("import-native")
def memory_import_native(
    source: list[str] = typer.Option(
        [], "--source", "-s",
        help="claude, cursor, gemini (repeatable; default: all)"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Import without asking"),
):
    """Import what another tool's own memory already learned (Claude Code
    auto-memory, Cursor rules, Gemini CLI's GEMINI.md files) as Cairn decisions,
    so every host sees them. A personal cross-project item (Gemini's
    ~/.gemini/GEMINI.md) becomes a global decision, never a committed project
    file. Shows candidates and asks before writing anything; already-imported,
    unchanged items are skipped."""
    from cairn.core import Project
    from cairn.memory import bridge

    for s in source:
        if s not in bridge.PARSERS:
            console.print(f"[red]Unknown source '{s}'. Choose from: {', '.join(bridge.PARSERS)}[/red]")
            raise typer.Exit(code=1)
    project = Project(load_session=False)
    mem = project.memory
    items = bridge.candidates(project.project_dir, source or None,
                              mem.global_db_path if mem.global_enabled else None)
    skipped = [i for i in items if i.get("scope") == "global" and not mem.global_enabled]
    items = [i for i in items if i not in skipped]
    if skipped:
        console.print(f"[yellow]{len(skipped)} personal item(s) skipped: global memory is off "
                      "(memory.global_enabled).[/yellow]")
    if not items:
        console.print("[green]Nothing new to import.[/green]")
        return
    console.print(f"[bold]{len(items)} candidate(s):[/bold]")
    for i in items:
        where = " (global)" if i.get("scope") == "global" else ""
        console.print(f"  [{i['source']}]{where} {i['decision']}", markup=False)
    if not yes:
        try:
            proceed = typer.confirm(f"Import {len(items)} as Cairn decisions?")
        except typer.Abort:
            proceed = False  # closed/absent stdin: decline, don't crash
        if not proceed:
            console.print("[yellow]Cancelled.[/yellow]")
            return
    for i in items:
        project.memory.record_decision(i["decision"], reasoning=i["reasoning"], recorded_by=i["source"],
                                       scope=i.get("scope", "project"))
    console.print(f"[green]Imported {len(items)} decision(s).[/green]")


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
