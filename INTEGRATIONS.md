# Cairn, host integration notes

Per-host quirks, workarounds, and verified versions for the MCP server.
Fill in during Phase 1 demo recording.

The setup is the same everywhere:

```bash
cd ~/your-project
cairn init
cairn mcp config --host <host>      # copy the printed config block (JSON or TOML, per host)
# paste into the host's MCP config; restart
```

Below: what to paste *where*, plus any host-specific gotchas.

---

## Claude Code

- **Config location:** `claude mcp add cairn -- cairn mcp serve` (recommended), or
  edit `.mcp.json` (project scope) / `~/.claude.json` (user scope, `mcpServers`
  key) directly. **Not** `claude_desktop_config.json`, that file belongs to
  Claude *Desktop*, a different app; using it here is a documented bug we fixed
  2026-08-09.
- **Restart needed:** yes, quit fully, reopen.
- **Verify:** in a new conversation, ask "what MCP tools do you have?", the
  response should list `memory_recall`, `memory_store`, etc.
- **Optional SessionStart hook:** `cairn mcp config --host claude-code --with-hooks`
  prints an opt-in recipe that runs `cairn memory boot` at session start, so
  memory loads without the agent needing to call a tool first. Doc-only -
  the command never writes to your config for you. Uses a `"command"`-type
  hook, not `"mcp_tool"`, Claude Code has a documented timing bug where
  `mcp_tool` SessionStart hooks can fire before the MCP server connects and
  get silently skipped ([anthropics/claude-code#57932](https://github.com/anthropics/claude-code/issues/57932)).
- **Verified versions:** not yet verified
- **Known quirks:** none yet.

---

## Cursor

- **Config location:** `~/.cursor/mcp.json`.
- **Restart needed:** yes.
- **Verify:** open the MCP panel in Cursor settings; `cairn` server should
  show "connected."
- **Optional SessionStart hook:** no verified recipe yet for Cursor -
  `cairn mcp config --host cursor --with-hooks` says so explicitly rather
  than guessing at unverified syntax. Contribute one if you've confirmed it.
- **Verified versions:** not yet verified
- **Known quirks:** not yet verified

---

## Continue.dev

- **Config location:** `~/.continue/config.json` (the `mcpServers` block -
  Continue adopted MCP in late 2025; check current docs if the schema moved).
- **Restart needed:** depends on host editor (VS Code / JetBrains).
- **Verify:** Continue's chat panel lists Cairn tools under "available tools."
- **Verified versions:** not yet verified
- **Known quirks:** not yet verified

---

## Codex CLI

- **Config location:** `~/.codex/config.toml` (global) or `.codex/config.toml`
  (project-scoped, requires `trust_level = "trusted"`). **TOML, not JSON** -
  `cairn mcp config --host codex` prints the correct `[mcp_servers.cairn]`
  block; do not paste the generic JSON snippet here.
- **Restart needed:** yes.
- **Verified versions:** not yet verified
- **Known quirks:** not yet verified, no confirmed SessionStart-equivalent
  hook recipe yet; contribute one if you've verified it.

---

## OpenCode

- **Config location:** `~/.config/opencode/opencode.json` (global) or a
  project-root `opencode.json` (highest precedence, shareable with a team).
- **Schema note:** OpenCode's `mcp` key is **not** the common `mcpServers`
  shape, it requires `"type": "local"` and combines `command`+`args` into
  **one array** (`"command": ["cairn", "mcp", "serve"]`), not separate
  fields. `cairn mcp config --host opencode` prints the correct shape.
- **Restart needed:** yes.
- **Verified versions:** not yet verified
- **Known quirks:** not yet verified, no confirmed SessionStart-equivalent
  hook recipe yet; contribute one if you've verified it.

---

## Windsurf

- **Config location:** `~/.codeium/windsurf/mcp_config.json` (`%USERPROFILE%\.codeium\windsurf\mcp_config.json`
  on Windows). Auto-created on first server added via the UI, or edit
  directly. Uses the standard `mcpServers` shape.
- **Restart needed:** yes.
- **Verify:** Command Palette → "Windsurf: Configure MCP Servers", or
  Cascade's MCP panel should show `cairn` connected.
- **Verified versions:** not yet verified
- **Known quirks:** not yet verified

---

## Zed

- **Config location:** `~/.config/zed/settings.json` (`%APPDATA%\Zed\settings.json`
  on Windows).
- **Schema note:** Zed uses a **`context_servers`** key, not `mcpServers` -
  a Zed-specific shape (verified against zed.dev/docs/ai/mcp, 2026-08-09).
  `cairn mcp config --host zed` prints the correct key.
- **Restart needed:** yes.
- **Verified versions:** not yet verified
- **Known quirks:** not yet verified

---

## Generic MCP host (Aider, custom CLI)

If your host speaks MCP over stdio using the common `mcpServers` shape,
this works:

```json
{
  "mcpServers": {
    "cairn": {
      "command": "cairn",
      "args": ["mcp", "serve"]
    }
  }
}
```

Anything that consumes that JSON shape (or an equivalent YAML form) will
launch Cairn as a subprocess and route tool calls to it.

---

## Verifying a host (checklist)

Every "Verified versions: not yet verified" row above needs the same six
steps, this turns filling one in into a ~10-minute mechanical job instead
of open-ended work. Do this once per host, on a real install of that host:

1. `cairn init` in a scratch project; `cairn mcp config --host <host>`.
2. Paste the printed block into the host's real config file (path is in
   that host's section above) and restart the host fully.
3. Ask the agent "what MCP tools do you have?", confirm the response lists
   Cairn tools (19 total; `cairn mcp config --host <host>` also prints the
   live count so you can cross-check).
4. Ask it to remember something (a fake decision), restart the host again,
   ask it to recall, confirms the round-trip actually persists.
5. If a hook recipe exists for this host (`--with-hooks`), verify memory
   loads automatically at a new session's start, without asking.
6. Record: host name + exact version tested, this repo's commit/version,
   OS, and any quirk hit, even a "worked exactly as documented" is worth
   recording (rules out "nobody tried it" from a future reader's mind).
   Update this file's "Verified versions" / "Known quirks" lines and open a
   PR, or paste the same into an issue if you'd rather not edit docs.

---

## Reporting an integration issue

Open an issue at <https://github.com/yourname/cairn/issues> with:

1. Host name + version (e.g., `Claude Code 1.4.2 on macOS 14.5`).
2. `cairn version` output.
3. Exact config block you pasted.
4. What you expected vs. what you saw (paste the host's MCP log if available).
