# Cairn: host integration notes

Per-host quirks, workarounds, and verified versions for the MCP server.
Fill in during Phase 1 demo recording.

The setup is the same everywhere:

```bash
cd ~/your-project
cairn init
cairn mcp config --host <host>      # copy the printed config block (JSON or TOML, per host)
# paste into the host's MCP config; restart
```

For Claude Code and Gemini CLI, `cairn init` offers to do the last two steps
for you (registers the MCP server and the hooks in this project only, keeping
whatever is already in those files); run it again in an existing project to be
offered the same. `cairn doctor` shows what is registered.

**Works in every host, no MCP or hooks needed:** `cairn init` (or `cairn sync`
in an existing project) keeps every active decision in a managed block of the
project's `AGENTS.md`. Antigravity (1.20.3+), Cursor, Codex and Copilot read
AGENTS.md at session start; Claude Code (2.1.277+) reads it when the project
has no CLAUDE.md, and when it has one Cairn adds an `@AGENTS.md` import to
it. **Gemini CLI is the one exception:** verified against a live install
(0.61.0, 2026-09-27), it reads only `GEMINI.md` by default, never AGENTS.md,
unless `context.fileName` in `.gemini/settings.json` lists it. `cairn
init`/`cairn sync` add that automatically once the project already has a
`GEMINI.md` or `.gemini/settings.json` (i.e. once it shows you use Gemini
CLI); they will not create `.gemini/` out of nothing for a project that
never has. Nothing per host to configure otherwise. `cairn doctor` shows what
is wired up, per host.

The MCP server and hooks below are additions on top of that: the MCP server
lets the agent record decisions and leave a handoff note, so the next tool
can pick up where this one stopped.

Below: what to paste *where*, plus any host-specific gotchas.

---

## Claude Code

- **Config location:** `claude mcp add cairn -- cairn mcp serve` (recommended), or
  edit `.mcp.json` (project scope) / `~/.claude.json` (user scope, `mcpServers`
  key) directly. **Not** `claude_desktop_config.json`: that file belongs to
  Claude *Desktop*, a different app; using it here is a documented bug we fixed
  2026-08-09.
- **Restart needed:** yes, quit fully, reopen.
- **Verify:** in a new conversation, ask "what MCP tools do you have?", the
  response should list `memory_recall`, `memory_store`, etc.
- **Optional SessionStart hook:** `cairn mcp config --host claude-code --with-hooks`
  prints an opt-in recipe that runs `cairn memory boot` at session start, so
  memory loads without the agent needing to call a tool first. Doc-only:
  the command never writes to your config for you. Uses a `"command"`-type
  hook, not `"mcp_tool"`, Claude Code has a documented timing bug where
  `mcp_tool` SessionStart hooks can fire before the MCP server connects and
  get silently skipped ([anthropics/claude-code#57932](https://github.com/anthropics/claude-code/issues/57932)).
  The same recipe adds a `PreToolUse` hook on `Edit|Write|MultiEdit` that runs
  `cairn memory for-path --hook`: before Claude edits a file, the decisions
  scoped to that file's directory are injected as `additionalContext`. It
  prints nothing when no decision applies, so it costs nothing on most edits.
  A `UserPromptSubmit` hook (`cairn memory detect`) flags decision-like
  language in the user's own message ("we decided...", "switch to X"), even
  when nobody asked to record it, quoting the matched phrase; pure pattern
  matching, no LLM call, silent on an ordinary message. A `Stop` hook
  (`cairn memory nudge`) reminds the agent to call `record_decision` after
  many edits with no decision recorded (default 8), at most once per
  threshold.
- **Verified versions:** not yet verified
- **Known quirks:** none yet.

---

## Cursor

- **Config location:** `~/.cursor/mcp.json`.
- **Restart needed:** yes.
- **Verify:** open the MCP panel in Cursor settings; `cairn` server should
  show "connected."
- **Optional SessionStart hook:** no verified recipe yet for Cursor:
  `cairn mcp config --host cursor --with-hooks` says so explicitly rather
  than guessing at unverified syntax. Contribute one if you've confirmed it.
- **Verified versions:** not yet verified
- **Known quirks:** not yet verified

---

## Continue.dev

- **Config location:** `~/.continue/config.json` (the `mcpServers` block:
  Continue adopted MCP in late 2025; check current docs if the schema moved).
- **Restart needed:** depends on host editor (VS Code / JetBrains).
- **Verify:** Continue's chat panel lists Cairn tools under "available tools."
- **Verified versions:** not yet verified
- **Known quirks:** not yet verified

---

## Codex CLI

- **Config location:** `~/.codex/config.toml` (global) or `.codex/config.toml`
  (project-scoped, requires `trust_level = "trusted"`). **TOML, not JSON**:
  `cairn mcp config --host codex` prints the correct `[mcp_servers.cairn]`
  block; do not paste the generic JSON snippet here.
- **Restart needed:** yes.
- **Verified versions:** not yet verified
- **Known quirks:** not yet verified, no confirmed SessionStart-equivalent
  hook recipe yet; contribute one if you've verified it.

---

## Gemini CLI

- **Config location:** `.gemini/settings.json` (project, default scope) or
  `~/.gemini/settings.json` (global). `cairn mcp config --host gemini-cli`
  prints the block, or run `gemini mcp add cairn -- cairn mcp serve`
  directly, both write the standard `mcpServers` shape.
- **AGENTS.md needs a settings key.** Unlike every other host above, Gemini
  CLI does not read AGENTS.md by default, only `GEMINI.md`
  (`context.fileName` in `settings.json` controls the list). `cairn
  init`/`cairn sync` add `"AGENTS.md"` to it automatically, but only once the
  project already has a `GEMINI.md` or `.gemini/settings.json`; `cairn
  doctor` reports whether it is missing.
- **The folder must be trusted, or all of that is silently ignored
  (verified 2026-09-28, 0.61.0; the mechanism read from source 2026-09-29).**
  In an untrusted folder Gemini CLI does not load the project's
  `.gemini/settings.json` at all: `context.fileName` (AGENTS.md is not
  auto-loaded) and `mcpServers` (Cairn's MCP server does not connect) both do
  nothing, with no warning in headless runs. Measured, not assumed: the same
  folder asked for a passphrase written only in AGENTS.md answered `UNKNOWN`
  untrusted and the passphrase once trusted, and a request to call a Cairn
  MCP tool made the model improvise ~11 shell commands untrusted versus one
  direct `mcp_cairn_decisions_for_path` call trusted. The block goes deeper
  than that one setting: read from the installed 0.61.0 bundle,
  `MemoryContextManager.discoverMemoryPaths()` skips the project-memory scan
  (where GEMINI.md and every name in `context.fileName` are found) entirely
  when `isTrustedFolder()` is false, so GEMINI.md itself, not just the
  setting that would add AGENTS.md to it, goes unread; a hand-written
  `@AGENTS.md` import line inside a committed GEMINI.md would fare no
  better. The private per-user memory folder
  (`~/.gemini/tmp/<project id>/memory/`) is a separate path list the same
  function does not gate this way. Interactively, run `gemini` in the
  project once and choose Trust folder. Headless or CI: set
  `GEMINI_CLI_TRUST_WORKSPACE=true`.
  `--skip-trust` is NOT equivalent: it only clears the "not running in a
  trusted directory" exit (and lets `--approval-mode yolo` apply), the
  folder stays untrusted for settings and MCP. Cairn's own benchmark ran its
  first Gemini sessions with `--skip-trust` alone and got runs where Gemini
  never saw AGENTS.md or the Cairn tools.
- **Hooks (verified 2026-09-28, 0.61.0).** `cairn init` registers one
  project hook, `BeforeAgent` running `cairn memory detect`: the same
  decision-language detector Claude Code gets on `UserPromptSubmit`. Gemini
  sends `{session_id, transcript_path, cwd, hook_event_name, timestamp,
  prompt}` on stdin; what Cairn prints as `hookSpecificOutput.additionalContext`
  is appended to that turn's user message as a `<hook_context>` block. Checked
  end to end with the hook `cairn init` itself wrote (for a `cairn` path with a
  space in it): the hook ran, received the real payload, and the reminder is in
  the request Gemini built for the model. Not observed: the model acting on
  it, because the free quota was exhausted and no reply came back. Gemini
  specifics: project hooks run only in a trusted folder; Gemini prints a
  warning listing the project's hooks when it first sees them (Cairn's is named
  `cairn-detect`); and it runs a hook command through PowerShell on Windows and
  bash elsewhere, so Cairn writes the path for that shell (forward slashes,
  and quoted with `&` when it contains a space). Not registered: a
  `Stop`-nudge equivalent (an `AfterAgent` hook whose exit code 2 retries the
  turn is documented, but was never run against a real model response) and any
  `SessionStart` or per-file `PreToolUse` equivalent. `cairn mcp config --host
  gemini-cli --with-hooks` prints the recipe for a manual setup.
- **Detected by:** the `gemini` executable on PATH, not a home directory
  marker (`~/.gemini` alone is ambiguous: Antigravity also uses it for its
  own knowledge folder without Gemini CLI being installed).
- **Comments in `settings.json`:** Gemini CLI accepts them (checked live), plain
  JSON does not. `cairn init` never rewrites such a file (it would drop the
  comments): it skips it and prints the `cairn mcp config` command to merge by
  hand, and `cairn doctor` names the file instead of guessing.
- **Restart needed:** yes, a running session does not pick up a new MCP
  server or a `context.fileName` change until it restarts (`/memory reload`
  reloads `GEMINI.md` content, not settings).
- **Verify:** `gemini mcp list` should show `cairn`; `/memory show` inside a
  session should include the AGENTS.md block's content once the
  `context.fileName` fix has landed and the session restarted.
- **Verified versions:** 0.61.0 (2026-09-27, MCP registration and the
  `context.fileName` fix; 2026-09-28, the `BeforeAgent` hook; headless multi-session automation and Gemini CLI's
  own native-memory format are separately tracked in `TASKS/IMPROVEMENT_PLAN.md` P10 step 5).
- **Known quirks:** headless mode (`gemini -p`) fails fast with a clear JSON
  error when unauthenticated rather than hanging, good for automation, and
  uses whatever auth is selected in `settings.json` (`security.auth.
  selectedType`) with a credential cached.
- **Gemini has its own idea of where a decision goes.** Read from the CLI's
  bundled system prompt (0.61.0): there is no `save_memory` tool; the model is
  told to edit markdown itself and to route a team-shared convention or
  architecture rule into the committed `./GEMINI.md`, a personal note into a
  private `MEMORY.md` under `~/.gemini/tmp/<id>/memory/`, and to write each
  fact to exactly one file. A decision stated to Gemini can therefore end up
  only in `GEMINI.md`, which Claude Code does not read. What pushes the other
  way is the AGENTS.md block's instruction to call `record_decision` (in the two
  `cairn_agents` cross-tool runs measured so far, one v1 pilot and one v2, Gemini
  recorded the reversal through Cairn's tools itself: n=2, not a rate) and now
  the `BeforeAgent` reminder (not measured against a real reply yet). Cairn does not import `GEMINI.md`: it is mostly
  hand-written project instructions, not decisions.
- **Free Google login is closed for personal accounts (verified 2026-09-28,
  0.61.0).** The CLI's own shipped `quota-and-pricing.md` still promises
  1,000 requests/day for "Sign in with Google", but the server now answers
  the sign-in with "This client is no longer supported for Gemini Code Assist
  for individuals... migrate to the Antigravity suite of products", even
  after Google's own browser step reports "Authentication succeeded". Do not
  trust that doc page for this. A failed attempt also leaves
  `selectedType: "oauth-personal"` behind in `~/.gemini/settings.json`,
  which breaks headless runs until the auth method is switched back (`/auth`
  inside `gemini`, then "Use Gemini API Key").
- **Free API key quota is tiny and per model per day.** `gemini-3.8-flash`
  (the current non-deprecated Flash; `gemini-2.5-flash` is rejected for new
  users) hit a terminal `You have exhausted your daily quota on this model`
  (`TerminalQuotaError`, reported limit 20) within a few real calls. The
  "Please retry in Ns" text that comes with it is misleading noise, the
  quota is daily; still exhausted 15+ hours after the failure that first hit
  it (checked 2026-09-28 21:37 and 21:43 UTC, i.e. before the doc's assumed
  midnight-Pacific/07:00-UTC reset), so treat "resets at midnight Pacific" as
  the vendor's claim, not independently confirmed. `--model auto` spends it
  faster: it also calls a `gemini-3.5-flash-lite` router each turn.
- **Pinning `--model gemini-3.5-flash-lite` does not stay pinned.** Checked
  live (2026-09-28): a one-turn, no-tool-call prompt ran entirely on
  `gemini-3.5-flash-lite` and succeeded even while the account's
  `gemini-3.8-flash` bucket was at 0/20. A second prompt that needed two
  `list_directory` tool calls ran the first two turns on
  `gemini-3.5-flash-lite`, then silently moved the next turn to
  `gemini-3.8-flash` (both models appear in that single run's own
  `stats.models`) and hit the same `TerminalQuotaError`. So the 20/day
  bucket is not only what `--model auto`'s router spends: a real
  multi-tool-call session on an explicitly pinned model can fall onto it
  too, meaning it takes very little agentic work per day, regardless of
  `--model`, to make every further session with 2+ tool calls fail until
  the reset. For automated or benchmark use, a pay-as-you-go API key is the
  reliable path.

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
- **Schema note:** Zed uses a **`context_servers`** key, not `mcpServers`:
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
   Cairn tools (14 total; `cairn mcp config --host <host>` also prints the
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
