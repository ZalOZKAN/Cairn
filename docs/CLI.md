# Cairn CLI Reference

This document provides a complete reference for the `cairn` command-line interface.

## Global Commands

### `cairn init`
Initialize Cairn in the current project directory.
- **What it does:** Creates the `.cairn/` configuration and persistence directory structure, generates a default `config.yaml` and `SYSTEM.md`, and adds the decisions block to `AGENTS.md` (creating the file if needed; see `cairn sync`). Then detects which MCP hosts are installed on the machine (by their own install directory, e.g. `~/.claude`, `~/.cursor`; Gemini CLI by the `gemini` executable on PATH); for Claude Code and Gemini CLI it asks once and, on yes, registers Cairn's MCP server and hooks in this project only: `.mcp.json` and `.claude/settings.json` for Claude Code; `.gemini/settings.json` for Gemini CLI (the `mcpServers` entry, a `BeforeAgent` decision-detector hook, and `AGENTS.md` added to `context.fileName`). Everything is merged in, so any other server, hook or setting already there is kept; a settings file that is not plain JSON (Gemini CLI itself accepts comments in `settings.json`) is skipped, with the `cairn mcp config` command to merge by hand, instead of being rewritten. Gemini CLI ignores a folder's `.gemini/settings.json` until the folder is trusted, so the command prints how (run `gemini` there once and choose Trust folder). Other detected hosts only get a pointer to `cairn mcp config --host <name>`. Declining, or no TTY to ask on, just skips this step; `.cairn/` is unaffected either way. Running `cairn init` again in a project that already has `.cairn/` skips the creation and offers the registration again (safe to repeat), which is what `cairn doctor` points to. `--dry-run` lists every file and setting it would create or change (including `~/.cairn/projects.json` and, if missing, `~/.cairn/global.db`) and exits without writing anything; the JSON steps run the same merges as the real run on an in-memory copy, in the same order, so the list is what `cairn init` then does.
- **Options:**
  - `--name, -n <text>`: Custom project name (defaults to directory name).
  - `--profile, -p [none|cloud|local]`: Optional helper model (default: `none`). Decisions, AGENTS.md and the handoff never need it. `cloud` configures the Groq API; `local` configures a local Ollama server (`qwen2.5:3b`) so the helper model never leaves the machine.
  - `--yes, -y`: Register detected hosts without asking.

### `cairn sync`
Sync `.cairn/decisions/` with the local index, then bring the project's `AGENTS.md` block in step with the decisions, both ways: first any bullet added, removed, or changed by hand since the last sync is read back into the decision records (added → recorded, removed → invalidated, one of each in the same section → the added one supersedes the removed one; all noted `recorded_by: agents.md`), then the block is rewritten in Cairn's format from the now-current records. Only the text between `<!-- cairn:begin -->` and `<!-- cairn:end -->` is Cairn's; the rest of the file is left untouched. If the project has a `CLAUDE.md` (or `.claude/CLAUDE.md`), Claude Code does not read AGENTS.md on its own, so Cairn adds an `@AGENTS.md` import to it, once. The block also regenerates automatically whenever a decision is recorded, invalidated, superseded, or pulled in from a teammate's decision files; if you delete the block, only `cairn sync` or `cairn init` adds it back. Set `memory.agents_md.enabled: false` to turn this off.

**Claude Code scoped rules (opt-in).** Set `memory.agents_md.claude_scoped_rules: true` in `.cairn/config.yaml` and `cairn sync` splits the Claude Code delivery: `AGENTS.md` keeps every decision (other tools read it), while `CLAUDE.md` (created if missing, Cairn's marker block only) holds just the repo-wide decisions, and each scoped decision goes to a path-scoped rule file, `.claude/rules/cairn/<scope>-<hash>.md` with `paths:` frontmatter, which Claude Code loads only when it reads a file under that path. Commit these files like `AGENTS.md`; they are regenerated, so edit decisions in `AGENTS.md` or with `cairn decide`, not in them. Files in that folder without Cairn's notice are never touched. Turning the key off restores the `@AGENTS.md` import and removes the rule files; `cairn uninstall` removes them too, and `cairn check` and `cairn doctor` cover them. Measured on the bench (haiku, 1,000 decisions, short tasks): 68% fewer total tokens than a flat file; no saving is claimed for a few hundred decisions or fewer, and other tools gain nothing.

### `cairn check`
Exit with status 1 if the AGENTS.md block is missing or does not match the decisions (hand-edited, or a decision file changed without a sync). Meant for CI.

### `cairn decide <decision>`
Record a decision from any shell; the same as the MCP `record_decision` tool, for agents without MCP. The decision file notes `recorded_by: cli`. If the decision replaces an active one and neither `--supersedes` nor `--replaces` was given, prints up to 3 candidates (same scope, meaningfully overlapping keywords, not just a word or two shared by chance) so you can invalidate the right one; nothing is ever invalidated automatically.
- **Options:**
  - `--why, -w <text>`: Why this was chosen.
  - `--rejected, -r <text>` / `--because, -b <text>`: A rejected option and why it lost; repeatable, matched in order.
  - `--path, -p <path>`: Workspace-relative path it applies to; omit for repo-wide.
  - `--supersedes <id>`: The decision this replaces, by id.
  - `--replaces <text>`: The decision this replaces, by a short phrase from it, when you don't have its id. Resolved by substring or keyword match; if that matches more than one decision, none is superseded and the candidates are printed instead.
  - `--importance, -i <1|2|3>`: Default 2.
  - `--forbid <regex>`: A pattern that code added under this decision must not match (repeatable), for a hard rule such as `--forbid '^\s*(import|from) tenacity\b'`. Checked against added lines only, never code already there: the per-file `PreToolUse` hook blocks such an edit, and `cairn memory guard` flags it in a diff. An invalid regex is rejected before anything is recorded.

### `cairn handoff [summary] --next <step>`
Leave a note for the next session, in any tool; the same as the MCP `memory_save` tool with a summary. Writes `.cairn/persistence/session-handoff.md` with the note plus sections Cairn fills in itself: the latest decisions (with the tool that recorded each) and the working tree (branch, uncommitted files, last commits). The MCP server refreshes those two sections whenever the host closes it, keeping the note.

### `cairn doctor`
Show what Cairn does in this project and what is off, with the reason: decision files, the AGENTS.md block, whether Claude Code will see it (CLAUDE.md import), whether Cairn is registered in `.mcp.json` and which of its 4 hooks are present in `.claude/settings.json`, the same for Gemini CLI (`.gemini/settings.json`: server, AGENTS.md in `context.fileName`, the `BeforeAgent` hook; a settings file that is not plain JSON is named as such rather than guessed at), any other detected host not yet set up, the last handoff, and the helper model (which features are skipped without one, or why a configured one will not work).

### `cairn uninstall [--yes]`
Undo what `cairn init` and `cairn sync` wired into this project: Cairn's block in `AGENTS.md`, the `@AGENTS.md` import in `CLAUDE.md` (or `.claude/CLAUDE.md`), the `cairn` entry under `mcpServers` in `.mcp.json` and `.gemini/settings.json`, and Cairn's own hook commands in `.claude/settings.json` and `.gemini/settings.json`, recognized by their subcommand whatever path the `cairn` executable had when they were written. Without `--yes` it only lists what it would remove. Two rules: nothing that is not Cairn's is touched (other servers, hooks, settings and text around the block, other tools' marker blocks such as OtherTool's, stay byte for byte), and a file left with nothing but Cairn's part is deleted rather than left empty. `AGENTS.md` in Gemini CLI's `context.fileName` carries no marker, so it goes only when Cairn recorded adding it (`.cairn/persistence/wiring.json`, per machine) or when `AGENTS.md` itself is gone; otherwise it may be yours and stays. A settings file that is not plain JSON is left alone with a note. `.cairn/` (your decisions and memory) is never deleted; `cairn init` then `cairn sync` wires the project back up. Hosts set up by hand in a user-level config (Cursor, Antigravity, Codex) keep their `cairn` entry.

### `cairn version`
Print the current version of Cairn.

---

## Session Memory Commands (`cairn memory`)

### `cairn memory save`
Run the extractor helper at the end of a session to save memory and write the continuation handoff. Needs the helper model unless `--no-extract --summary` is used; `cairn handoff` is the simpler, model-free way to leave a note.
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
Print every active decision scoped to a file's directory (or its ancestors), never truncated. With `--hook` it runs as a Claude Code `PreToolUse` hook: reads the hook JSON on stdin and prints `hookSpecificOutput.additionalContext`, or nothing when no decision applies. Each decision is shown at most once per session (tracked by the hook's `session_id` in `.cairn/persistence/hook-seen.json`), so repeated edits don't re-inject the same reminder. Every hook run that gets past the `forbid` check is counted in `stats.json` as `for_path_hook` (calls = runs, returned something = runs that injected a decision), so `cairn memory stats` shows whether the hook fires at all. It also enforces `forbid` patterns: when the edit adds a line that an active decision applying to the file forbids, it exits 2 with the decision and its reasoning on stderr, which Claude Code treats as "block this tool call and show the model why" (verified live, haiku, 2026-09-30: the edit was not applied and the model asked the user instead of retrying). `cairn memory boot` includes every active decision (the same text as the AGENTS.md block), so no decision is left out of the boot context. `--full` (with or without `--hook`) prints each decision as the AGENTS.md block shows it (the decision, why, rejected alternatives, forbidden patterns, plus the staleness flag) instead of one line; the default output is unchanged.

### `cairn memory for-diff [range] [--staged]`
Show the active decisions that apply to the files a diff changes, each decision once with the changed files that triggered it. For a host that has no per-edit hook, for you before a commit, or for CI/review: `decisions_for_path` for a whole change at once. Pure git plus the decision records, no LLM call. Repo-wide decisions are left out (they are in `AGENTS.md` and the boot context already); so are `.cairn/`'s own files, since recording a decision is itself an uncommitted change.
- **Arguments:** `range` (optional): any `git diff` revision or range, e.g. `main..HEAD`, `HEAD~3`. With none, all uncommitted work counts, including files git does not track yet (but not ignored ones); renames count as both the old and the new path.
- **Options:** `--staged`: only what is staged (not together with a range, exit 2).
- **Exit status:** 1 when git cannot produce the diff (not a repository, unknown revision), so a script can tell that apart from "no decision applies" (exit 0).

### `cairn memory detect`
Claude Code `UserPromptSubmit` / Gemini CLI `BeforeAgent` hook (reads the hook JSON on stdin; both send the user's message as `prompt`). If the user's own message reads like a decision stated in passing ("from now on...", "we agreed...", "switch to X", "let's use X"), even without asking for it to be recorded, prints `hookSpecificOutput.additionalContext` reminding the agent to call `record_decision` and quoting the matched phrase, tagged with the event name the host sent; silent otherwise. Pure pattern matching, no LLM call, so it costs nothing on an ordinary message. Included in `cairn mcp config --host claude-code --with-hooks` and `--host gemini-cli --with-hooks`.

### `cairn memory nudge`
Claude Code `Stop` hook (reads the hook JSON on stdin). When the session has made at least `memory.nudge.edits` file edits (default 8) since the last `record_decision`, it exits with code 2 and a one-line reminder, so the agent records any decision it just settled. Fires at most once per threshold, never while already continuing from a nudge, and is silent otherwise. Included in `cairn mcp config --host claude-code --with-hooks`. It also reads a Gemini CLI transcript (`write_file` and `replace` count as edits), but Cairn does not register it for Gemini CLI: the equivalent there is an `AfterAgent` hook whose exit code 2 retries the turn, documented but not yet run against a real model response.

### `cairn memory capture --from-git <range>`
Reads `git log` for a revision range (default `HEAD~20..HEAD`), asks the helper model to propose design decisions, and shows each one for you to confirm. Nothing is recorded without a yes. Exits with a message when no helper model is configured.

### `cairn memory stats`
Local usage counters from `.cairn/persistence/stats.json` (gitignored, never sent anywhere): how many times the agent called each Cairn MCP tool and how often it returned something, plus the same two numbers for the hooks Cairn registers (`guard`: edits checked and blocked; `for_path_hook`: runs and runs that injected a decision), and the number of decision files. Use it to judge whether the agent is actually recording and using decisions.

### `cairn memory stale`
List active decisions scoped to a path (`scope_path`) whose path has received at least `memory.stale.commits` commits (default 10) since the decision was recorded or last confirmed. Such decisions are also marked `NEEDS REVIEW` in recall output. Nothing is ever invalidated automatically. Repo-wide decisions and non-git projects are never flagged.

### `cairn memory confirm <id>`
Mark a decision as still valid: re-anchors it to the current git HEAD, which resets its staleness counter (the anchor is saved in the decision file, so teammates get it too).

### `cairn memory import-adr <dir>`
Import existing Architecture Decision Records (Nygard or MADR style) from a folder such as `docs/adr` as Cairn decisions. Proposed/draft ADRs are skipped; re-running is a no-op for ADRs already imported.

### `cairn memory guard [range] [--staged] [--hook]`
Fail (exit 1) when a change adds a line that an active decision's `forbid` pattern matches: repo-wide decisions and ones scoped to the file's directory or an ancestor. Only added lines count, so a legacy file a decision says to leave alone keeps its old code, and `.cairn/`, `AGENTS.md`, `CLAUDE.md` and `GEMINI.md` are never checked (describing a rule there is not breaking it). Same range rules as `for-diff`: a revision or range, `--staged` for a git pre-commit hook, or all uncommitted work (untracked files counted whole). `--hook` reads a Claude Code `PreToolUse` or Gemini CLI `BeforeTool` payload on stdin instead and exits 2 to block the edit; `cairn init` registers it for Gemini CLI on `write_file|replace`, while Claude Code gets the same check through `for-path --hook`. An edit-tool hook does not see a file written through a shell command (observed live: Gemini CLI, blocked on `write_file`, wrote the file with PowerShell instead), so run `cairn memory guard --staged` as a pre-commit hook, or `cairn memory guard origin/main...HEAD` in CI, as the check nothing can route around.

### `cairn memory import-native [--source claude|cursor|gemini] [--yes]`
Import what another tool's own persistent memory already learned (Claude Code's per-project auto-memory, Cursor rules) as ordinary Cairn decisions, with `recorded_by` set to the source. Shows every candidate and asks before recording anything, unless `--yes` is given. `--source` is repeatable; default is all three. Re-running is a no-op for anything whose text has not changed since it was imported. Cursor support is built from Cursor's documented rules format, not verified against a live install. Gemini CLI has no save-memory tool: the model writes top-level bullets into markdown files, routed by the phrasing of what it was told (read from its 0.61.0 system prompt; the global tier checked against a note the real CLI wrote). Imported: the project's `GEMINI.md` as project decisions, and `~/.gemini/GEMINI.md` (personal, cross-project) as global decisions, so a personal preference never lands in the repo's committed files. Not imported: subdirectory `GEMINI.md` files, and the private project memory under `~/.gemini/tmp/<project id>/memory/`, which Gemini keeps out of the repo by definition. Bullets inside fenced code or inside another tool's marker block (Cairn's and other tools') are skipped.

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
  - `--host, -h <name>`: `claude-code` | `cursor` | `antigravity` | `codex` | `opencode` | `windsurf` | `zed` | `gemini-cli` | `generic` (default: `claude-code`).
  - `--with-hooks`: also print an opt-in hook recipe. For `claude-code`: `SessionStart` runs `cairn memory boot`, `PreToolUse` on `Edit|Write|MultiEdit` runs `cairn memory for-path --hook`, `UserPromptSubmit` runs `cairn memory detect`, `Stop` runs `cairn memory nudge`. For `gemini-cli`: only `BeforeAgent` running `cairn memory detect`. Doc-only, never writes to any config file. Currently verified for `claude-code` and `gemini-cli` only; other hosts print an explicit "not yet verified" note rather than guessed syntax.
  - `--output, -o <path>`: write the config block to a file instead of the terminal. Useful when the resolved `cairn` path is long enough that a terminal would otherwise wrap it; still needs to be merged into the host's own config file by hand, nothing is edited automatically.
- **Format note:** most hosts use JSON (`mcpServers` block); `codex` uses TOML (`[mcp_servers.cairn]` in `config.toml`), the command emits the correct format per host, it does not always print JSON.
- **Claude Code note:** the printed instructions point at `claude mcp add` / `.mcp.json` / `~/.claude.json`, not `claude_desktop_config.json`, which is Claude *Desktop*'s file, a different app.
