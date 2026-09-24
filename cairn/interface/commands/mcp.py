"""cairn mcp, run Cairn as an MCP server, or print client config."""
from __future__ import annotations

import json
import shutil

import typer
from rich.console import Console

mcp_app = typer.Typer(help="Run Cairn as an MCP server for any MCP host")
console = Console()

_ALL_HOSTS = [
    "claude-code", "cursor", "antigravity", "codex", "opencode", "windsurf", "zed", "generic",
]

# Config snippet shape per host, verified against each host's own docs
# (2026-08-09), not assumed. Three genuinely different JSON shapes exist
# (plus Codex's TOML), the Step-1 bug (wrong path/format for Claude Code
# and Codex) made "just print the same JSON everywhere" the thing to never
# repeat here.
_TOML_HOSTS = {"codex"}

_JSON_SNIPPET_BUILDERS = {
    # Standard "mcpServers": {name: {command, args}} shape.
    "claude-code": lambda cmd: {"mcpServers": {"cairn": {"command": cmd, "args": ["mcp", "serve"]}}},
    "cursor": lambda cmd: {"mcpServers": {"cairn": {"command": cmd, "args": ["mcp", "serve"]}}},
    "antigravity": lambda cmd: {"mcpServers": {"cairn": {"command": cmd, "args": ["mcp", "serve"]}}},
    "windsurf": lambda cmd: {"mcpServers": {"cairn": {"command": cmd, "args": ["mcp", "serve"]}}},
    "generic": lambda cmd: {"mcpServers": {"cairn": {"command": cmd, "args": ["mcp", "serve"]}}},
    # Zed: "context_servers" key, not "mcpServers" (zed.dev/docs/ai/mcp).
    "zed": lambda cmd: {"context_servers": {"cairn": {"command": cmd, "args": ["mcp", "serve"]}}},
    # OpenCode: "mcp" key, requires "type": "local", command+args combined
    # into ONE array, not separate fields (opencode.ai/docs/mcp-servers).
    "opencode": lambda cmd: {"mcp": {"cairn": {"type": "local", "command": [cmd, "mcp", "serve"]}}},
}

# Opt-in SessionStart hook recipes, An earlier project decision rejected
# IDE hooks as a *default* trigger (5/6 against) but explicitly allowed
# doc-only opt-in. type="command" (not "mcp_tool"), Claude Code has a
# documented race where SessionStart mcp_tool hooks can fire before the MCP
# server connects and get silently skipped (anthropics/claude-code#57932).
_HOOK_SNIPPETS = {
    "claude-code": lambda cmd: json.dumps({
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": f"{cmd} memory boot"}]}
            ],
            # Injects the decisions scoped to the file about to be edited.
            "PreToolUse": [
                {"matcher": "Edit|Write|MultiEdit",
                 "hooks": [{"type": "command", "command": f"{cmd} memory for-path --hook"}]}
            ],
            # Reminds the agent to record decisions after many unrecorded edits.
            "Stop": [
                {"hooks": [{"type": "command", "command": f"{cmd} memory nudge"}]}
            ],
        }
    }, indent=2),
}

_HOOK_UNVERIFIED_NOTE = (
    "No verified SessionStart-equivalent hook recipe for this host yet. "
    "Memory still surfaces without one, the agent can call `memory_boot` "
    "itself at any point, this only removes the need to ask. If you've "
    "confirmed a working recipe on this host, please contribute it (PR)."
)

_HOST_NOTES = {
    "claude-code": (
        'Run `claude mcp add cairn -- {cmd} mcp serve` (recommended), or add '
        'the block below to `.mcp.json` (project scope, key "mcpServers") or '
        '`~/.claude.json` (user scope). Note: this is Claude Code, not Claude '
        "Desktop, Desktop's config lives elsewhere."
    ),
    "cursor": "Add the block below to ~/.cursor/mcp.json.",
    "antigravity": "Add the block below to ~/.antigravity/mcp.json (path may vary by version).",
    "codex": (
        "Add the block below to ~/.codex/config.toml (applies to all sessions) "
        'or .codex/config.toml (project-scoped; set trust_level = "trusted").'
    ),
    "opencode": (
        "Add the block below to ~/.config/opencode/opencode.json (global) or "
        'a project-root opencode.json (highest precedence). Note the "type": '
        '"local" field and that command+args are ONE array, not separate keys, '
        "OpenCode's schema, not the common mcpServers one."
    ),
    "windsurf": "Add the block below to ~/.codeium/windsurf/mcp_config.json.",
    "zed": (
        'Add the block below to ~/.config/zed/settings.json. Note the key is '
        '"context_servers", not "mcpServers", Zed-specific.'
    ),
    "generic": "Add the block below to your MCP host's config file.",
}


def _tool_names() -> list[str]:
    """Derive the tool list from the live schema so this can never drift."""
    from cairn.mcp.server import CairnMCPServer
    return [t["name"] for t in CairnMCPServer()._tools]


@mcp_app.command("serve")
def mcp_serve():
    """Start the Cairn MCP server over stdio (Claude Code-compatible)."""
    from cairn.mcp.server import CairnMCPServer

    CairnMCPServer().run()


@mcp_app.command("config")
def mcp_config(
    host: str = typer.Option(
        "claude-code", "--host", "-h",
        help="claude-code | cursor | antigravity | codex | opencode | windsurf | zed | generic",
    ),
    with_hooks: bool = typer.Option(
        False, "--with-hooks",
        help="Also print an opt-in SessionStart hook recipe (doc-only, never installs anything)",
    ),
):
    """Print an MCP client config snippet for the chosen host."""
    cairn_path = shutil.which("cairn") or "cairn"

    note = _HOST_NOTES.get(host, _HOST_NOTES["generic"]).format(cmd=cairn_path)
    console.print(f"[bold]{note}[/bold]\n")

    if host in _TOML_HOSTS:
        console.print(
            f'[mcp_servers.cairn]\ncommand = "{cairn_path}"\nargs = ["mcp", "serve"]',
            markup=False,
        )
    else:
        build = _JSON_SNIPPET_BUILDERS.get(host, _JSON_SNIPPET_BUILDERS["generic"])
        console.print(json.dumps(build(cairn_path), indent=2), markup=False)

    tools = _tool_names()
    console.print(f"\n[dim]Tools become available ({len(tools)}):[/dim] " + ", ".join(tools))

    if with_hooks:
        console.print(
            "\n[bold]Optional: SessionStart hook (opt-in, doc-only, this "
            "command does not modify any config file for you):[/bold]"
        )
        recipe = _HOOK_SNIPPETS.get(host)
        if recipe is None:
            console.print(f"[dim]{_HOOK_UNVERIFIED_NOTE}[/dim]", markup=False)
        else:
            console.print(recipe(cairn_path), markup=False)
