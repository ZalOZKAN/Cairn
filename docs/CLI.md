# Cairn CLI Reference

This document provides a complete reference for the `cairn` command-line interface.

## Global Commands

### `cairn init`
Initialize Cairn in the current project directory.
- **What it does:** Creates the `.cairn/` configuration and persistence directory structure and generates a default `config.yaml` and `SYSTEM.md`.
- **Options:**
  - `--name, -n <text>`: Custom project name (defaults to directory name).
  - `--profile, -p [cloud|local]`: Helper model profile (default: `cloud`). `cloud` configures the Groq API; `local` configures a local Ollama server (`qwen2.5:3b`) so the helper model never leaves the machine. See `profiles/local_first.yaml` for the underlying template.

### `cairn version`
Print the current version of Cairn.

---

## Session Memory Commands (`cairn memory`)

### `cairn memory save`
Run the extractor helper at the end of a session to save memory and write the continuation handoff.
- **Options:**
  - `--summary, -s <text>`: Provide a manual summary of the session.
  - `--no-extract`: Skip the LLM extraction step and write the handoff with only the manual summary.

### `cairn memory boot`
Generate the session boot context to copy/inject into your AI assistant at the start of a session.
- **Options:**
  - `--format, -f [text|claude|json]`: Select output format (default: `text`).
  - `--copy, -c`: Copy the generated output directly to the system clipboard (requires `pyperclip`).
  - `--memory, -m <text>`: Load extra memory files from `.cairn/persistence/memory/` on boot.

### `cairn memory status`
Show the active session task, status, session count, and a preview of the last handoff.

### `cairn memory manifest`
Create, regenerate, or show the project context manifest (`context-manifest.md`).
- **Options:**
  - `--regen`: Force regeneration of the manifest file by scanning files in the project.

### `cairn memory search <query>`
Search for keywords or substrings across both the SQLite database and session memory files. Results include matches from `~/.cairn/global.db` (cross-project memory) when `memory.global_enabled` is true, the results table's `Scope` column shows `project` or `global`.
- **Arguments:**
  - `query` (Required): Search term.
- **Options:**
  - `--limit, -n <int>`: Max results to return (default: `5`).
  - `--path, -p <path>`: Scope results to a workspace-relative path (e.g. `apps/api`); repo-wide entries still match.

### `cairn memory check <approach>`
Check whether an approach was already tried and rejected (a decision's rejected alternative) or abandoned (a decision later invalidated/superseded). Read-only, no LLM call, checks project + global DBs.
- **Arguments:**
  - `approach` (Required): The approach being considered, in a sentence or two.
- **Options:**
  - `--limit, -n <int>`: Max matches to return (default: `5`).

### `cairn memory recent`
Show a table of the most recent memory entries recorded in the SQLite database.
- **Options:**
  - `--n, -n <int>`: Number of recent entries to show (default: `10`).

### `cairn memory invalidate <id>`
Invalidate a memory record (e.g. fact or decision) by its row ID, hiding it from recall.
- **Arguments:**
  - `id` (Required): Database row ID, a plain number for a project entry, or `g<n>` (e.g. `g7`) for a global entry.
- **Options:**
  - `--reason, -r <text>`: Optional rationale for why the decision or fact is no longer valid.

### `cairn memory dashboard`
Generate the project decision dashboard (interactive single-file HTML).
- **Options:**
  - `--open`: Open the generated dashboard in the default web browser automatically.
  - `--filter [all|active|invalidated|recent]`: Filter decisions shown in the dashboard.

### `cairn memory export`
Sync decisions with `.cairn/decisions/`, one Markdown file per decision. Files are the source of truth: new or hand-edited files are imported, decisions without a file are written out. This also runs automatically whenever Cairn opens a project, so you rarely need it by hand.

### `cairn memory for-path <path>`
Print the active decisions scoped to a file's directory (or its ancestors). With `--hook` it runs as a Claude Code `PreToolUse` hook: reads the hook JSON on stdin and prints `hookSpecificOutput.additionalContext`, or nothing when no decision applies. `cairn memory boot` now also lists active decisions (importance 2+).

### `cairn memory nudge`
Claude Code `Stop` hook (reads the hook JSON on stdin). When the session has made at least `memory.nudge.edits` file edits (default 8) since the last `record_decision`, it exits with code 2 and a one-line reminder, so the agent records any decision it just settled. Fires at most once per threshold, never while already continuing from a nudge, and is silent otherwise. Included in `cairn mcp config --host claude-code --with-hooks`.

### `cairn memory capture --from-git <range>`
Reads `git log` for a revision range (default `HEAD~20..HEAD`), asks the helper model to propose design decisions, and shows each one for you to confirm. Nothing is recorded without a yes. Exits with a message when no helper model is configured.

### `cairn memory stats`
Local usage counters from `.cairn/persistence/stats.json` (gitignored, never sent anywhere): how many times the agent called each Cairn MCP tool and how often it returned something, plus the number of decision files. Use it to judge whether the agent is actually recording and using decisions.

### `cairn memory stale`
List active decisions scoped to a path (`scope_path`) whose path has received at least `memory.stale.commits` commits (default 10) since the decision was recorded or last confirmed. Such decisions are also marked `NEEDS REVIEW` in recall output. Nothing is ever invalidated automatically. Repo-wide decisions and non-git projects are never flagged.

### `cairn memory confirm <id>`
Mark a decision as still valid: re-anchors it to the current git HEAD, which resets its staleness counter (the anchor is saved in the decision file, so teammates get it too).

### `cairn memory import-adr <dir>`
Import existing Architecture Decision Records (Nygard or MADR style) from a folder such as `docs/adr` as Cairn decisions. Proposed/draft ADRs are skipped; re-running is a no-op for ADRs already imported.

### `cairn memory reindex`
Drop the decision rows from the SQLite index and rebuild them from `.cairn/decisions/*.md`. Use after a messy merge or if the local DB is lost.

### `cairn memory clear`
Delete all SQLite memory entries. Requires confirmation unless bypassed.
- **Options:**
  - `--yes, -y`: Bypass confirmation prompt.

---

## MCP Server Commands (`cairn mcp`)

### `cairn mcp serve`
Start the Cairn stdio MCP server. This is the command that MCP hosts (like Claude Code, Cursor) execute in the background to access your project's memory.

### `cairn mcp config`
Print an MCP client config snippet for the chosen host, plus the live list of available tools (so the count can never go stale).
- **Options:**
  - `--host, -h <name>`: `claude-code` | `cursor` | `antigravity` | `codex` | `opencode` | `windsurf` | `zed` | `generic` (default: `claude-code`).
  - `--with-hooks`: also print an opt-in SessionStart hook recipe (runs `cairn memory boot` automatically at session start). Doc-only, never writes to any config file. Currently verified for `claude-code` only; other hosts print an explicit "not yet verified" note rather than guessed syntax.
- **Format note:** most hosts use JSON (`mcpServers` block); `codex` uses TOML (`[mcp_servers.cairn]` in `config.toml`), the command emits the correct format per host, it does not always print JSON.
- **Claude Code note:** the printed instructions point at `claude mcp add` / `.mcp.json` / `~/.claude.json`, not `claude_desktop_config.json`, which is Claude *Desktop*'s file, a different app.
