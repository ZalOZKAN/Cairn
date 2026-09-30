"""cairn mcp: run Cairn as an MCP server, or print client config."""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import typer
from rich.console import Console

mcp_app = typer.Typer(help="Run Cairn as an MCP server for any MCP host")
console = Console()

_ALL_HOSTS = [
    "claude-code", "cursor", "antigravity", "codex", "opencode", "windsurf", "zed", "gemini-cli", "generic",
]

# Config snippet shape per host, verified against each host's own docs
# (2026-08-09), not assumed. Three genuinely different JSON shapes exist
# (plus Codex's TOML): the Step-1 bug (wrong path/format for Claude Code
# and Codex) made "just print the same JSON everywhere" the thing to never
# repeat here.
_TOML_HOSTS = {"codex"}

_JSON_SNIPPET_BUILDERS = {
    # Standard "mcpServers": {name: {command, args}} shape.
    "claude-code": lambda cmd: {"mcpServers": {"cairn": {"command": cmd, "args": ["mcp", "serve"]}}},
    "cursor": lambda cmd: {"mcpServers": {"cairn": {"command": cmd, "args": ["mcp", "serve"]}}},
    "antigravity": lambda cmd: {"mcpServers": {"cairn": {"command": cmd, "args": ["mcp", "serve"]}}},
    "windsurf": lambda cmd: {"mcpServers": {"cairn": {"command": cmd, "args": ["mcp", "serve"]}}},
    # Verified live (gemini-cli 0.61.0, 2026-09-27): `gemini mcp add` writes
    # exactly this shape to .gemini/settings.json.
    "gemini-cli": lambda cmd: {"mcpServers": {"cairn": {"command": cmd, "args": ["mcp", "serve"]}}},
    "generic": lambda cmd: {"mcpServers": {"cairn": {"command": cmd, "args": ["mcp", "serve"]}}},
    # Zed: "context_servers" key, not "mcpServers" (zed.dev/docs/ai/mcp).
    "zed": lambda cmd: {"context_servers": {"cairn": {"command": cmd, "args": ["mcp", "serve"]}}},
    # OpenCode: "mcp" key, requires "type": "local", command+args combined
    # into ONE array, not separate fields (opencode.ai/docs/mcp-servers).
    "opencode": lambda cmd: {"mcp": {"cairn": {"type": "local", "command": [cmd, "mcp", "serve"]}}},
}

# Opt-in hook recipes. An earlier project decision rejected
# IDE hooks as a *default* trigger (5/6 against) but explicitly allowed
# doc-only opt-in. type="command" (not "mcp_tool"): Claude Code has a
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
            # Flags decision-like language in the user's own message ("we
            # decided...", "switch to X"), even when nobody asked to record it.
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": f"{cmd} memory detect"}]}
            ],
            # Reminds the agent to record decisions after many unrecorded edits.
            "Stop": [
                {"hooks": [{"type": "command", "command": f"{cmd} memory nudge"}]}
            ],
        }
    }, indent=2),
    # Gemini CLI: only the detector, the one hook checked live (0.61.0,
    # 2026-09-28): BeforeAgent receives {session_id, transcript_path, cwd,
    # hook_event_name, timestamp, prompt}, and its additionalContext reached
    # the request sent to the model. The AfterAgent equivalent of the Stop
    # nudge (exit code 2 + stderr = retry turn) is documented but has not
    # been run against a real model response yet, so it is not registered.
    # BeforeTool, checked live (0.61.0, 2026-09-30): exit code 2 blocks
    # write_file and shows stderr to the model; the payload carries
    # tool_input.file_path/content, the shape `memory guard --hook` reads. The
    # blocked model then wrote the file through run_shell_command, which no
    # edit-tool hook sees: the git-level `cairn memory guard --staged` is the
    # backstop for that.
    "gemini-cli": lambda cmd: json.dumps({
        "hooks": {
            "BeforeAgent": [
                {"matcher": "*",
                 "hooks": [{"name": "cairn-detect", "type": "command", "command": f"{cmd} memory detect"}]}
            ],
            "BeforeTool": [
                {"matcher": "write_file|replace",
                 "hooks": [{"name": "cairn-guard", "type": "command", "command": f"{cmd} memory guard --hook"}]}
            ],
        }
    }, indent=2),
}

_HOOK_UNVERIFIED_NOTE = (
    "No verified SessionStart-equivalent hook recipe for this host yet. "
    "Memory still surfaces without one: the agent can call `memory_boot` "
    "itself at any point. This only removes the need to ask. If you've "
    "confirmed a working recipe on this host, please contribute it (PR)."
)

_HOST_NOTES = {
    "claude-code": (
        'Run `claude mcp add cairn -- {cmd} mcp serve` (recommended), or add '
        'the block below to `.mcp.json` (project scope, key "mcpServers") or '
        '`~/.claude.json` (user scope). Note: this is Claude Code, not Claude '
        "Desktop. Desktop's config lives elsewhere."
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
        '"local" field and that command+args are ONE array, not separate keys: '
        "OpenCode's schema, not the common mcpServers one."
    ),
    "windsurf": "Add the block below to ~/.codeium/windsurf/mcp_config.json.",
    "gemini-cli": (
        'Add the block below to .gemini/settings.json (project) or ~/.gemini/settings.json '
        '(global), or run `gemini mcp add cairn -- {cmd} mcp serve`. Gemini CLI reads only '
        "GEMINI.md by default, not AGENTS.md; `cairn init`/`cairn sync` add AGENTS.md to "
        "context.fileName automatically once a GEMINI.md or .gemini/ shows you use it. "
        "Gemini CLI ignores this folder's .gemini/settings.json (AGENTS.md and the Cairn MCP "
        "server) until the folder is trusted: run `gemini` here once and choose Trust folder, "
        "or set GEMINI_CLI_TRUST_WORKSPACE=true for headless runs (--skip-trust is not enough)."
    ),
    "zed": (
        'Add the block below to ~/.config/zed/settings.json. Note the key is '
        '"context_servers", not "mcpServers": Zed-specific.'
    ),
    "generic": "Add the block below to your MCP host's config file.",
}


def _tool_names() -> list[str]:
    """Derive the tool list from the live schema so this can never drift."""
    from cairn.mcp.server import CairnMCPServer
    return [t["name"] for t in CairnMCPServer()._tools]


# ---- one-command setup ----------------------------------------------------
# A host's own install directory existing under the home directory (verified
# against this machine for claude-code and against each host's docs for the
# rest, 2026-09-27). Only proof of an install at some point, not current use;
# good enough to decide whether setup is worth offering.
_HOME_MARKERS = {
    "claude-code": ".claude",
    "cursor": ".cursor",
    "antigravity": ".antigravity",
    "codex": ".codex",
    "opencode": ".config/opencode",
    "windsurf": ".codeium/windsurf",
    "zed": ".config/zed",
}


def detect_hosts(home: Path | None = None, which=shutil.which) -> list[str]:
    """Installed hosts, by their own marker directory under `home`. Gemini
    CLI is checked by its executable on PATH instead (`which`, overridable
    for tests the same way `home` is): `~/.gemini` also holds Antigravity's
    knowledge folder (verified on a real machine that has Antigravity but not
    Gemini CLI, 2026-09-27), so a bare `~/.gemini` directory cannot tell the
    two apart."""
    home = home or Path.home()
    found = [h for h, rel in _HOME_MARKERS.items() if (home / rel).is_dir()]
    if which("gemini"):
        found.append("gemini-cli")
    return found


def _load_json_object(path: Path) -> dict:
    """`{}` for a missing file; raises on one that exists but will not parse,
    so a caller never merges into (and overwrites) a file it could not read."""
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not plain JSON ({exc}); comments are not supported here") from exc
    return data if isinstance(data, dict) else {}


def merge_mcp_server(data: dict, cairn_path: str) -> str:
    """Cairn's entry into a `{"mcpServers": {...}}` object; every other key
    and server is left untouched. Returns "added", "updated" (an existing
    cairn entry pointed elsewhere), or "already" (no change needed)."""
    servers = data.setdefault("mcpServers", {})
    entry = {"command": cairn_path, "args": ["mcp", "serve"]}
    if servers.get("cairn") == entry:
        return "already"
    action = "updated" if "cairn" in servers else "added"
    servers["cairn"] = entry
    return action


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _install_mcp_servers_json(path: Path, cairn_path: str) -> str:
    data = _load_json_object(path)
    action = merge_mcp_server(data, cairn_path)
    if action != "already":
        _write_json(path, data)
    return action


def install_mcp_json(project_dir: Path, cairn_path: str) -> str:
    """Merge Cairn into `.mcp.json` (Claude Code project scope)."""
    return _install_mcp_servers_json(project_dir / ".mcp.json", cairn_path)


def install_gemini_mcp_json(project_dir: Path, cairn_path: str) -> str:
    """Merge Cairn into `.gemini/settings.json`'s `mcpServers` (verified live
    against `gemini mcp add`, 0.61.0, 2026-09-27: same shape and file `gemini
    mcp add --scope project` itself writes to)."""
    return _install_mcp_servers_json(project_dir / ".gemini" / "settings.json", cairn_path)


_HOOK_SETTINGS = {
    "claude-code": Path(".claude") / "settings.json",
    "gemini-cli": Path(".gemini") / "settings.json",
}


def hook_exe(cairn_path: str, host: str, windows: bool = os.name == "nt") -> str:
    """`cairn_path` as the first word of a hook command. Unlike an MCP server
    entry (spawned directly), a hook command is one string run through a
    shell: bash, except Gemini CLI on Windows, which uses PowerShell (its hook
    runner, 0.61.0). Forward slashes read the same to both, where bash would
    eat backslashes; a path with spaces needs each shell's own quoting, and
    PowerShell also needs `&` to call a quoted path."""
    exe = cairn_path.replace("\\", "/") if windows else cairn_path
    if not any(c.isspace() or c in "'\"&;()$`" for c in exe):
        return exe
    if host == "gemini-cli" and windows:
        return "& '" + exe.replace("'", "''") + "'"
    return "'" + exe.replace("'", "'\\''") + "'"


def _cairn_hook_groups(cairn_path: str, host: str = "claude-code") -> dict[str, dict]:
    """{event: hook-group} straight from the host's recipe (kept whole, e.g.
    Claude Code's PreToolUse "matcher" field), the one place both the
    installer and `cairn doctor` read this from, so they cannot drift apart."""
    recipe = json.loads(_HOOK_SNIPPETS[host](hook_exe(cairn_path, host)))
    return {event: groups[0] for event, groups in recipe["hooks"].items()}


def _hook_args() -> set[str]:
    """What follows the executable in each of Cairn's hook commands, e.g.
    "memory detect", read from the recipes themselves."""
    return {h["command"].split(" ", 1)[1]
            for host in _HOOK_SNIPPETS
            for group in _cairn_hook_groups("cairn", host).values()
            for h in group["hooks"]}


def is_cairn_hook_command(command: str) -> bool:
    """Whether a hook command is one of Cairn's own, whatever path the
    executable had when it was written: plain, quoted, PowerShell's
    `& '...'`, or `python -m cairn.interface.cli`."""
    for args in _hook_args():
        if command.endswith(" " + args):
            exe = command[: -len(args)].strip()
            break
    else:
        return False
    exe = exe.removeprefix("&").strip().strip("'\"")
    if re.search(r"-m\s+cairn(\.interface\.cli)?$", exe):
        return True
    return re.split(r"[\\/]", exe)[-1].lower() in {"cairn", "cairn.exe", "cairn.cmd", "cairn.bat"}


def _present_commands(data: dict) -> set[tuple[str, str]]:
    hooks = data.get("hooks", {})
    present = set()
    for event, groups in hooks.items() if isinstance(hooks, dict) else []:
        for group in groups if isinstance(groups, list) else []:
            for h in group.get("hooks", []) if isinstance(group, dict) else []:
                if h.get("command"):
                    present.add((event, h["command"]))
    return present


def _hook_status(project_dir: Path, cairn_path: str, host: str) -> dict[str, bool]:
    """{event: is cairn's exact command for it already present}. A missing or
    unparseable settings file reads as all False rather than raising, since
    this is a read-only status check."""
    try:
        data = _load_json_object(project_dir / _HOOK_SETTINGS[host])
    except ValueError:
        data = {}
    present = _present_commands(data)
    return {
        event: (event, group["hooks"][0]["command"]) in present
        for event, group in _cairn_hook_groups(cairn_path, host).items()
    }


def merge_hooks(data: dict, cairn_path: str, host: str) -> list[str]:
    """Cairn's hook groups appended to each event's own list rather than
    replacing it, so hooks already configured for the same event are kept.
    Returns the events that got one."""
    present = _present_commands(data)
    added = []
    for event, group in _cairn_hook_groups(cairn_path, host).items():
        if (event, group["hooks"][0]["command"]) in present:
            continue
        data.setdefault("hooks", {}).setdefault(event, []).append(group)
        added.append(event)
    return added


def _install_hooks(project_dir: Path, cairn_path: str, host: str) -> str:
    """Returns "added" or "already" (every command was already present)."""
    path = project_dir / _HOOK_SETTINGS[host]
    data = _load_json_object(path)
    if not merge_hooks(data, cairn_path, host):
        return "already"
    _write_json(path, data)
    return "added"


def claude_hook_status(project_dir: Path, cairn_path: str) -> dict[str, bool]:
    return _hook_status(project_dir, cairn_path, "claude-code")


def install_claude_hooks(project_dir: Path, cairn_path: str) -> str:
    """Cairn's hooks in `.claude/settings.json`."""
    return _install_hooks(project_dir, cairn_path, "claude-code")


def gemini_hook_status(project_dir: Path, cairn_path: str) -> dict[str, bool]:
    return _hook_status(project_dir, cairn_path, "gemini-cli")


def install_gemini_hooks(project_dir: Path, cairn_path: str) -> str:
    """Cairn's hooks in `.gemini/settings.json` (same nested `hooks` shape as
    Claude Code's, checked against Gemini CLI 0.61.0's own hooks reference)."""
    return _install_hooks(project_dir, cairn_path, "gemini-cli")


@mcp_app.command("serve")
def mcp_serve():
    """Start the Cairn MCP server over stdio (Claude Code-compatible)."""
    from cairn.mcp.server import CairnMCPServer

    CairnMCPServer().run()


@mcp_app.command("config")
def mcp_config(
    host: str = typer.Option(
        "claude-code", "--host", "-h",
        help=" | ".join(_ALL_HOSTS),
    ),
    with_hooks: bool = typer.Option(
        False, "--with-hooks",
        help="Also print an opt-in hook recipe (doc-only, never installs anything)",
    ),
    output: Path = typer.Option(
        None, "--output", "-o",
        help="Write the config block to this file instead of the terminal (still needs to be "
             "merged into the host's own config file by hand; nothing is edited automatically)",
    ),
):
    """Print an MCP client config snippet for the chosen host."""
    cairn_path = shutil.which("cairn") or "cairn"

    note = _HOST_NOTES.get(host, _HOST_NOTES["generic"]).format(cmd=cairn_path)
    console.print(f"[bold]{note}[/bold]\n", soft_wrap=True)

    if host in _TOML_HOSTS:
        block = f'[mcp_servers.cairn]\ncommand = "{cairn_path}"\nargs = ["mcp", "serve"]'
    else:
        build = _JSON_SNIPPET_BUILDERS.get(host, _JSON_SNIPPET_BUILDERS["generic"])
        block = json.dumps(build(cairn_path), indent=2)

    if output:
        output.write_text(block + "\n", encoding="utf-8")
        console.print(f"[green]Config block written to {output}[/green]", soft_wrap=True)
    else:
        console.print(block, markup=False, soft_wrap=True)

    tools = _tool_names()
    console.print(f"\n[dim]Tools become available ({len(tools)}):[/dim] " + ", ".join(tools))

    if with_hooks:
        console.print(
            "\n[bold]Optional: hooks (opt-in, doc-only, this "
            "command does not modify any config file for you):[/bold]"
        )
        recipe = _HOOK_SNIPPETS.get(host)
        if recipe is None:
            console.print(f"[dim]{_HOOK_UNVERIFIED_NOTE}[/dim]", markup=False)
        else:
            console.print(recipe(hook_exe(cairn_path, host)), markup=False, soft_wrap=True)
