"""cairn init: initialize a project."""
from __future__ import annotations

from pathlib import Path
import typer
from rich.console import Console

console = Console()

_CONFIG_BASE = """\
# Cairn config: drop this in any project's .cairn/ folder.
# Docs: https://github.com/yourusername/cairn
# Profile: {profile}. `cairn doctor` shows which features are on.

memory:
  enabled: true
  backend: sqlite        # or "hybrid" for SQLite + ChromaDB semantic search
  async_store: true
  sliding_window: 10
  global_enabled: true   # merge in ~/.cairn/global.db (cross-project prefs/facts)
  redact:
    enabled: true         # best-effort secret masking before anything is written
    extra_patterns: []    # [[regex, replacement], ...] for project-specific secret formats
  decay:
    enabled: true
    half_life_days: 30
  dedup:
    enabled: true
    threshold: 0.92
  agents_md:
    enabled: true         # keep every active decision in a managed block of AGENTS.md

mcp:
  tools: core             # "core": the 6 tools that carry decisions (about 1,000 tokens per request); "all": all 14

# Optional helper model, used only for batch housekeeping (session extraction,
# manifest, `cairn memory capture --from-git`, conflict detection). Decisions,
# AGENTS.md and the cross-tool handoff never need it.
{helper}
"""

NONE_CONFIG_TEMPLATE = _CONFIG_BASE.format(profile="none (default)", helper="""\
# Off. To enable later: pip install cairn[helper], then either
#   memory_helper_model: groq/llama-3.3-70b-versatile   (export GROQ_API_KEY=..., free tier)
#   memory_helper_model: ollama/qwen2.5:3b              (local, needs Ollama)
memory_helper_model: null""")

CLOUD_CONFIG_TEMPLATE = _CONFIG_BASE.format(profile="cloud", helper="""\
# Setup: pip install cairn[helper]; export GROQ_API_KEY=... (free tier)
memory_helper_model: groq/llama-3.3-70b-versatile""")

LOCAL_CONFIG_TEMPLATE = _CONFIG_BASE.format(profile="local", helper="""\
# Nothing leaves the machine. Setup: pip install cairn[helper];
# ollama pull qwen2.5:3b; ollama serve
memory_helper_model: ollama/qwen2.5:3b""")

# Folder format rule: volatile data stays local, decisions get committed.
GITIGNORE_TEMPLATE = """persistence/memory.db
persistence/memory.db-journal
persistence/memory.db-wal
persistence/memory.db-shm
persistence/chroma/
persistence/nudge-state.json
persistence/hook-seen.json
persistence/stats.json
persistence/wiring.json
*.jsonl
"""

PROFILE_TEMPLATES = {
    "none": NONE_CONFIG_TEMPLATE,
    "cloud": CLOUD_CONFIG_TEMPLATE,
    "local": LOCAL_CONFIG_TEMPLATE,
}

SYSTEM_MD_TEMPLATE = """\
# {project_name}: project context

Fill this with the long-lived facts about your project: stack, key
architecture decisions, naming conventions, deploy targets, gotchas.
Cairn reads this at session start so your AI agent doesn't have
to re-derive it every time.

## Stack
- (write here)

## Key files
- (write here)

## Conventions
- (write here)

## Current state
- (write here)
"""


def _setup_claude_code(cairn_path: str) -> None:
    from cairn.interface.commands import mcp as mcp_cmd
    mcp_action = mcp_cmd.install_mcp_json(Path.cwd(), cairn_path)
    hook_action = mcp_cmd.install_claude_hooks(Path.cwd(), cairn_path)
    console.print(f"  .mcp.json: {mcp_action}", markup=False)
    console.print(f"  .claude/settings.json hooks: {hook_action}", markup=False)


def _setup_gemini_cli(cairn_path: str) -> None:
    from cairn.interface.commands import mcp as mcp_cmd
    from cairn.memory import agents_md
    mcp_action = mcp_cmd.install_gemini_mcp_json(Path.cwd(), cairn_path)
    console.print(f"  .gemini/settings.json mcpServers: {mcp_action}", markup=False)
    hook_action = mcp_cmd.install_gemini_hooks(Path.cwd(), cairn_path)
    console.print(f"  .gemini/settings.json hooks: {hook_action}", markup=False)
    if agents_md.link_gemini(Path.cwd()) is not None:
        console.print("  .gemini/settings.json context.fileName: added AGENTS.md", markup=False)
    console.print("  note: Gemini CLI ignores this until the folder is trusted: run `gemini` here "
                  "once and choose Trust folder (headless: GEMINI_CLI_TRUST_WORKSPACE=true).",
                  markup=False)


AUTOMATED_SETUP = {"claude-code": _setup_claude_code, "gemini-cli": _setup_gemini_cli}


def _register_hosts(yes: bool) -> None:
    """Offer to register Cairn's MCP server and hooks with the hosts found on
    this machine, in the current directory only. Idempotent, so it also serves
    a project whose `.cairn/` already exists (what `cairn doctor` tells the
    user to re-run)."""
    from cairn.interface.commands import mcp as mcp_cmd
    hosts = mcp_cmd.detect_hosts()
    if not hosts:
        return
    console.print("")
    console.print(f"[bold]Detected:[/bold] {', '.join(hosts)}")
    automatable = [h for h in hosts if h in AUTOMATED_SETUP]
    if automatable:
        # typer.confirm raises Abort on a closed/absent stdin (no TTY, or a
        # script that never piped anything); .cairn/ already exists at this
        # point, so that must not turn into a non-zero exit for the whole
        # command, only into "skip this optional registration".
        try:
            register = yes or typer.confirm(
                f"Register Cairn's MCP server and hooks for {', '.join(automatable)} (this project only)?")
        except typer.Abort:
            register = False
        if register:
            import shutil as _shutil
            cairn_path = _shutil.which("cairn") or "cairn"
            for host in automatable:
                try:
                    AUTOMATED_SETUP[host](cairn_path)
                except ValueError as exc:
                    # A settings file that will not parse as plain JSON (Gemini CLI
                    # itself accepts comments in settings.json, which rewriting
                    # would drop) is never overwritten.
                    console.print(f"  {host}: skipped, {exc}", markup=False)
                    console.print(f"  set it up by hand: cairn mcp config --host {host} --with-hooks",
                                  markup=False)
    for h in hosts:
        if h not in AUTOMATED_SETUP:
            console.print(f"  {h} detected: run `cairn mcp config --host {h} --with-hooks` "
                          "to set up by hand", markup=False)


def _plan(project_dir: Path, home: Path, yes: bool) -> tuple[dict[str, list[str]], list[str]]:
    """({path: [what]}, notes) for everything `init` would write here, in the
    order it writes. JSON steps run the same merges the real run uses, on an
    in-memory copy, so a later step sees an earlier one's effect (Gemini's
    host setup creates the settings file its context link then needs)."""
    from cairn.interface.commands import mcp as mcp_cmd
    from cairn.memory import agents_md
    rows: dict[str, list[str]] = {}
    notes: list[str] = []
    copies: dict[Path, dict | None] = {}

    def add(rel: str, labels: list[str]) -> None:
        if labels:
            rows.setdefault(rel, []).extend(x for x in labels if x not in rows.get(rel, []))

    def edit(rel: str, merge) -> None:
        path = project_dir / rel
        if path not in copies:
            try:
                copies[path] = mcp_cmd._load_json_object(path)
            except ValueError:
                copies[path] = None
                notes.append(f"{rel} is not plain JSON: init would skip it")
        if copies[path] is not None:
            add(rel, merge(copies[path]))

    def mcp_entry(data: dict) -> list[str]:
        action = mcp_cmd.merge_mcp_server(data, cairn_path)
        return {"added": ["mcpServers.cairn"], "updated": ["mcpServers.cairn (repointed)"]}.get(action, [])

    def hooks(host: str):
        return lambda data: ([f"hooks ({', '.join(e)})"] if (e := mcp_cmd.merge_hooks(data, cairn_path, host)) else [])

    def context(data: dict) -> list[str]:
        return ["context.fileName AGENTS.md"] if agents_md.merge_gemini_context(data) else []

    import shutil as _shutil
    cairn_path = _shutil.which("cairn") or "cairn"
    gemini = ".gemini/settings.json"
    if not (project_dir / ".cairn").exists():
        add(".cairn/", ["config.yaml, SYSTEM.md, .gitignore, memory/, persistence/"])
        add("~/.cairn/projects.json", ["this project's entry (the dashboard's project list)"])
        if not (home / ".cairn" / "global.db").exists():
            add("~/.cairn/global.db", ["new (cross-project memory)"])
        agents = agents_md.path(project_dir)
        add("AGENTS.md", ["Cairn's decisions block" + ("" if agents.is_file() else " (new file)")])
        claude = agents_md.claude_link_target(project_dir)
        if claude is not None:
            add(claude.relative_to(project_dir).as_posix(), ["@AGENTS.md import"])
        if agents_md.gemini_in_use(project_dir):
            edit(gemini, context)
    else:
        notes.append(".cairn/ already exists: only host registration runs (`cairn sync` refreshes AGENTS.md)")

    hosts = mcp_cmd.detect_hosts()
    automatable = [h for h in hosts if h in AUTOMATED_SETUP]
    if automatable and not yes:
        notes.append(f"asks before registering {', '.join(automatable)} (--yes skips the question)")
    for host in automatable:
        if host == "claude-code":
            edit(".mcp.json", mcp_entry)
            if copies.get(project_dir / ".mcp.json") is not None:
                edit(".claude/settings.json", hooks("claude-code"))
        elif host == "gemini-cli":
            edit(gemini, mcp_entry)
            if copies.get(project_dir / gemini) is not None:
                edit(gemini, hooks("gemini-cli"))
                edit(gemini, context)
    for h in hosts:
        if h not in AUTOMATED_SETUP:
            notes.append(f"{h} detected: not set up automatically (`cairn mcp config --host {h} --with-hooks`)")
    return rows, notes


def init_cmd(
    project_name: str = typer.Option("", "--name", "-n", help="Project name"),
    profile: str = typer.Option(
        "none",
        "--profile",
        "-p",
        help="Helper model: 'none' (default, not needed for decisions or handoff), "
             "'cloud' (Groq API) or 'local' (Ollama, fully offline)",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y",
        help="Register Cairn's MCP server and hooks for detected hosts without asking",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="List every file init would create or change, then exit without writing anything",
    ),
):
    """Initialize Cairn in the current directory."""
    if profile not in PROFILE_TEMPLATES:
        console.print(
            f"[red]Unknown profile '{profile}'. Choose one of: {', '.join(PROFILE_TEMPLATES)}[/red]"
        )
        raise typer.Exit(code=1)

    if dry_run:
        rows, notes = _plan(Path.cwd(), Path.home(), yes)
        console.print("[bold]Would create or change:[/bold]" if rows else "Nothing to write here.")
        for rel, labels in rows.items():
            console.print(f"  {rel}: {', '.join(labels)}", markup=False)
        for note in notes:
            console.print(f"  note: {note}", markup=False)
        console.print("Dry run: nothing was written.", markup=False)
        return

    cairn_dir = Path.cwd() / ".cairn"

    if cairn_dir.exists():
        console.print("[yellow].cairn/ already exists in this directory. "
                      "Run `cairn sync` to refresh AGENTS.md.[/yellow]")
        _register_hosts(yes)
        return

    (cairn_dir / "memory").mkdir(parents=True)
    (cairn_dir / "persistence" / "memory").mkdir(parents=True)

    # config.yaml
    config_path = cairn_dir / "config.yaml"
    config_path.write_text(PROFILE_TEMPLATES[profile], encoding="utf-8")

    # SYSTEM.md
    name = project_name or Path.cwd().name
    system_path = cairn_dir / "SYSTEM.md"
    system_path.write_text(
        SYSTEM_MD_TEMPLATE.format(project_name=name),
        encoding="utf-8",
    )

    # .gitignore: volatile data stays local, decisions and the handoff get committed
    (cairn_dir / ".gitignore").write_text(GITIGNORE_TEMPLATE, encoding="utf-8")

    from cairn.config.registry import register_project
    register_project(Path.cwd(), Path("~/.cairn/projects.json").expanduser())

    from cairn.core import Project
    from cairn.interface.commands.project import link_agents_md
    project = Project(load_session=False)
    linked = link_agents_md(project)
    project.close()

    console.print("[green]Created .cairn/[/green]")
    console.print(f"  config      : {config_path}  [dim](profile: {profile})[/dim]")
    console.print(f"  system      : {system_path}")
    console.print(f"  persistence : {cairn_dir / 'persistence'}")
    for name in linked:
        console.print(f"  decisions   : {name}", markup=False)
    console.print("[dim]  AGENTS.md is read by Antigravity, Cursor, Claude Code, Codex, Copilot and others;[/dim]")
    console.print("[dim]  Cairn only edits the block between its <!-- cairn:begin/end --> markers.[/dim]")

    _register_hosts(yes)

    console.print("")
    console.print("[bold]Next:[/bold]")
    if profile == "local":
        console.print("  ollama pull qwen2.5:3b                [dim]# one-time, for the helper model[/dim]")
    console.print("  cairn memory manifest                [dim]# scan project and write context-manifest.md[/dim]")
    console.print("  cairn mcp config --host claude-code  [dim]# print MCP client config[/dim]")
    console.print("  cairn mcp serve                      [dim]# (your MCP host invokes this for you)[/dim]")
