# Cairn, host integration notes

Per-host quirks, workarounds, and verified versions for the MCP server.
Fill in during Phase 1 demo recording.

The setup is the same everywhere:

```bash
cd ~/your-project
cairn init
cairn mcp config --host <host>      # copy the printed JSON
# paste into the host's MCP config; restart
```

Below: what to paste *where*, plus any host-specific gotchas.

---

## Claude Code

- **Config location:** `~/.claude/claude_desktop_config.json` (macOS / Linux)
  or `%APPDATA%\Claude\claude_desktop_config.json` (Windows).
- **Restart needed:** yes, quit fully, reopen.
- **Verify:** in a new conversation, ask "what MCP tools do you have?", the
  response should list `memory_recall`, `memory_store`, etc.
- **Verified versions:** not yet verified
- **Known quirks:** none yet.

---

## Cursor

- **Config location:** `~/.cursor/mcp.json`.
- **Restart needed:** yes.
- **Verify:** open the MCP panel in Cursor settings; `cairn` server should
  show "connected."
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

## Generic MCP host (Aider, Windsurf, custom CLI)

If your host speaks MCP over stdio, this works:

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

## Reporting an integration issue

Open an issue at <https://github.com/yourname/cairn/issues> with:

1. Host name + version (e.g., `Claude Code 1.4.2 on macOS 14.5`).
2. `cairn version` output.
3. Exact config block you pasted.
4. What you expected vs. what you saw (paste the host's MCP log if available).
