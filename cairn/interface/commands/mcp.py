"""cairn mcp, run Cairn as an MCP server, or print client config."""
from __future__ import annotations

import json
import shutil

import typer
from rich.console import Console

mcp_app = typer.Typer(help="Run Cairn as an MCP server for any MCP host")
console = Console()


@mcp_app.command("serve")
def mcp_serve():
    """Start the Cairn MCP server over stdio (Claude Code-compatible)."""
    from cairn.mcp.server import CairnMCPServer

    CairnMCPServer().run()


@mcp_app.command("config")
def mcp_config(
    host: str = typer.Option("claude-code", "--host", "-h",
                             help="claude-code | cursor | antigravity | generic"),
):
    """Print an MCP client config snippet for the chosen host."""
    cairn_path = shutil.which("cairn") or "cairn"
    snippet = {
        "mcpServers": {
            "cairn": {
                "command": cairn_path,
                "args": ["mcp", "serve"],
            }
        }
    }

    target = {
        "claude-code": "~/.claude/claude_desktop_config.json",
        "cursor": "~/.cursor/mcp.json",
        "antigravity": "~/.antigravity/mcp.json (path may vary)",
        "generic": "your MCP host's config file",
    }.get(host, "your MCP host's config file")

    console.print(f"[bold]Add this to {target}:[/bold]")
    console.print(json.dumps(snippet, indent=2))
    console.print(
        "\n[dim]Tools become available as:[/dim] "
        "memory_recall, memory_store, memory_recent, memory_search, "
        "memory_invalidate, memory_boot, memory_status, list_skills, "
        "get_skill, list_workflows, get_workflow, list_agents, get_agent"
    )
