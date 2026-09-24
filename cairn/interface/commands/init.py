"""cairn init: initialize a project."""
from __future__ import annotations

from pathlib import Path
import typer
from rich.console import Console

console = Console()

CLOUD_CONFIG_TEMPLATE = """\
# Cairn config: drop this in any project's .cairn/ folder.
# Docs: https://github.com/yourusername/cairn
# Profile: cloud (default). For a fully local/offline helper model, run
# `cairn init --profile local` instead (requires Ollama).

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

# The single optional model Cairn uses for memory housekeeping
# (extractor, manifest, summarization, conflict detection). Set to null
# to disable all of these and run purely as a file-format / MCP server.
# Setup: pip install cairn[helper]; export GROQ_API_KEY=... (free tier)
memory_helper_model: groq/llama-3.3-70b-versatile
"""

LOCAL_CONFIG_TEMPLATE = """\
# Cairn config: drop this in any project's .cairn/ folder.
# Docs: https://github.com/yourusername/cairn
# Profile: local. Nothing leaves the machine: the helper model runs
# through a local Ollama server instead of a cloud API.

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

# The single optional model Cairn uses for memory housekeeping
# (extractor, manifest, summarization, conflict detection). Set to null
# to disable all of these and run purely as a file-format / MCP server.
# Setup: pip install cairn[helper]; ollama pull qwen2.5:3b; ollama serve
memory_helper_model: ollama/qwen2.5:3b
"""

# Folder format rule: volatile data stays local, decisions get committed.
GITIGNORE_TEMPLATE = """persistence/memory.db
persistence/memory.db-journal
persistence/memory.db-wal
persistence/memory.db-shm
persistence/chroma/
persistence/nudge-state.json
persistence/stats.json
*.jsonl
"""

PROFILE_TEMPLATES = {
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


def init_cmd(
    project_name: str = typer.Option("", "--name", "-n", help="Project name"),
    profile: str = typer.Option(
        "cloud",
        "--profile",
        "-p",
        help="Helper model profile: 'cloud' (Groq API, default) or 'local' (Ollama, fully offline)",
    ),
):
    """Initialize Cairn in the current directory."""
    if profile not in PROFILE_TEMPLATES:
        console.print(
            f"[red]Unknown profile '{profile}'. Choose one of: {', '.join(PROFILE_TEMPLATES)}[/red]"
        )
        raise typer.Exit(code=1)

    cairn_dir = Path.cwd() / ".cairn"

    if cairn_dir.exists():
        console.print("[yellow].cairn/ already exists in this directory.[/yellow]")
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

    # .gitignore
    gitignore = cairn_dir / ".gitignore"
    gitignore.write_text("memory/\n*.db\n*.jsonl\n", encoding="utf-8")

    from cairn.config.registry import register_project
    register_project(Path.cwd(), Path("~/.cairn/projects.json").expanduser())

    console.print("[green]Created .cairn/[/green]")
    console.print(f"  config      : {config_path}  [dim](profile: {profile})[/dim]")
    console.print(f"  system      : {system_path}")
    console.print(f"  persistence : {cairn_dir / 'persistence'}")
    console.print("")
    console.print("[bold]Next:[/bold]")
    if profile == "local":
        console.print("  ollama pull qwen2.5:3b                [dim]# one-time, for the helper model[/dim]")
    console.print("  cairn memory manifest                [dim]# scan project and write context-manifest.md[/dim]")
    console.print("  cairn mcp config --host claude-code  [dim]# print MCP client config[/dim]")
    console.print("  cairn mcp serve                      [dim]# (your MCP host invokes this for you)[/dim]")
