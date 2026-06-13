"""cairn init, initialize a project."""
from __future__ import annotations

from pathlib import Path
import typer
from rich.console import Console

console = Console()

CONFIG_TEMPLATE = """\
# Cairn config, drop this in any project's .cairn/ folder.
# Docs: https://github.com/yourusername/cairn

memory:
  enabled: true
  backend: sqlite        # or "hybrid" for SQLite + ChromaDB semantic search
  async_store: true
  sliding_window: 10
  decay:
    enabled: true
    half_life_days: 30
  dedup:
    enabled: true
    threshold: 0.92

# The single optional model Cairn uses for memory housekeeping
# (extractor, manifest, summarization, conflict detection). Set to null
# to disable all of these and run purely as a file-format / MCP server.
memory_helper_model: groq/llama-3.3-70b-versatile
"""

SYSTEM_MD_TEMPLATE = """\
# {project_name}, project context

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
):
    """Initialize Cairn in the current directory."""
    cairn_dir = Path.cwd() / ".cairn"

    if cairn_dir.exists():
        console.print("[yellow].cairn/ already exists in this directory.[/yellow]")
        return

    (cairn_dir / "skills").mkdir(parents=True)
    (cairn_dir / "workflows").mkdir(parents=True)
    (cairn_dir / "memory").mkdir(parents=True)
    (cairn_dir / "persistence" / "memory").mkdir(parents=True)
    (cairn_dir / "agents").mkdir(parents=True)

    # config.yaml
    config_path = cairn_dir / "config.yaml"
    config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")

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

    console.print("[green]Created .cairn/[/green]")
    console.print(f"  config      : {config_path}")
    console.print(f"  system      : {system_path}")
    console.print(f"  persistence : {cairn_dir / 'persistence'}")
    console.print("")
    console.print("[bold]Next:[/bold]")
    console.print("  cairn memory manifest                [dim]# scan project and write context-manifest.md[/dim]")
    console.print("  cairn mcp config --host claude-code  [dim]# print MCP client config[/dim]")
    console.print("  cairn mcp serve                      [dim]# (your MCP host invokes this for you)[/dim]")
