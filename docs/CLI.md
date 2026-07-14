# Cairn CLI Reference

This document provides a complete reference for the `cairn` command-line interface.

## Global Commands

### `cairn init`
Initialize Cairn in the current project directory.
- **What it does:** Creates the `.cairn/` configuration and directory structures (skills, agents, workflows, persistence) and generates a default `config.yaml` and `SYSTEM.md`.
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
Search for keywords or substrings across both the SQLite database and session memory files.
- **Arguments:**
  - `query` (Required): Search term.
- **Options:**
  - `--limit, -n <int>`: Max results to return (default: `5`).

### `cairn memory recent`
Show a table of the most recent memory entries recorded in the SQLite database.
- **Options:**
  - `--n, -n <int>`: Number of recent entries to show (default: `10`).

### `cairn memory invalidate <id>`
Invalidate a memory record (e.g. fact or decision) by its row ID, hiding it from recall.
- **Arguments:**
  - `id` (Required): Database row ID.
- **Options:**
  - `--reason, -r <text>`: Optional rationale for why the decision or fact is no longer valid.

### `cairn memory dashboard`
Generate the project decision dashboard (interactive single-file HTML).
- **Options:**
  - `--open`: Open the generated dashboard in the default web browser automatically.
  - `--filter [all|active|invalidated|recent]`: Filter decisions shown in the dashboard.

### `cairn memory export`
Export all decisions from the SQLite database to a plain-text markdown file at `.cairn/persistence/memory/decisions.md` so that they can be tracked, diffed, and versioned in Git.

### `cairn memory clear`
Delete all SQLite memory entries. Requires confirmation unless bypassed.
- **Options:**
  - `--yes, -y`: Bypass confirmation prompt.

---

## MCP Server Commands (`cairn mcp`)

### `cairn mcp serve`
Start the Cairn stdio MCP server. This is the command that MCP hosts (like Claude Code, Cursor) execute in the background to access your project's memory.

### `cairn mcp config`
Print recommended MCP client configurations for Claude Desktop, Claude Code, Cursor, and Continue.
