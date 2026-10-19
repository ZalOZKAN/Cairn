# Cairn

*Stones on the trail, for your AI.*

A portable `.cairn/` folder you drop into any repo so your AI coding agent
remembers your decisions across sessions.

---

> **Monday.** You and Claude Code spend an hour deciding to use httpOnly
> cookies for JWT instead of localStorage. You weigh the XSS implications,
> settle the call, move on.
>
> **Friday.** You reopen the project. The agent has no idea this decision
> was made. You ask "should we use localStorage for the new auth flow?" and
> it happily suggests yes. You re-explain.

Multiply by every decision in a three-month project. Your agent has no
memory across sessions, so *you* become the memory, and you're worse at it
than a database. Cairn fixes that with a local, MCP-native memory store
that lives next to your code.

---

## 30-second install

```bash
pip install cairn-memory
cd ~/my-project
cairn init                             # also adds a decisions block to AGENTS.md
```

`cairn init` alone already gives every agent your decisions: they are kept
in a managed block of `AGENTS.md`, the file Antigravity, Cursor, Claude Code,
Codex and Copilot read. Gemini CLI reads only `GEMINI.md` by default; once a
project shows you use it (a `GEMINI.md` or `.gemini/` already there), `cairn
init`/`cairn sync` also add AGENTS.md to its `context.fileName`, no manual
settings edit needed (Gemini CLI applies a folder's `.gemini/settings.json`
only once you trust the folder: run `gemini` there once and choose Trust
folder, or set `GEMINI_CLI_TRUST_WORKSPACE=true` in headless runs). If it
detects Claude Code or Gemini CLI, it also asks
once to register the MCP server and hooks for this
project (run `cairn init` again later to be asked again), so the agent gets fourteen memory tools (six of them, the ones that carry decisions, in a new project's default `mcp.tools: core` profile; see `docs/CLI.md`) (`memory_recall`,
`memory_store`, `memory_invalidate`, `record_decision`, `decisions_for_path`,
`memory_thread`, `check_approach`, and friends) after just a restart, no
config file to hand-edit. Any other
host it detects (Cursor, Antigravity, Codex, ...) gets a one-line pointer to
`cairn mcp config --host <name>`: print a snippet, paste it into that host's
own MCP config, restart.

![Claude Code demo](docs/demos/claude-code.gif)

---

## Switch tools, keep the memory

Start in Antigravity, continue in Claude Code, finish in Cursor. What moves
with the project:

- **Decisions.** Every active decision is in `AGENTS.md`, in full, the moment
  it is recorded, whichever tool recorded it (each record notes its source).
  Superseded and invalidated decisions drop out, so the file never carries
  two contradicting rules the way a hand-kept notes file does.
- **Where you left off.** `.cairn/persistence/session-handoff.md` holds the
  last agent's note (what was done, the next step) plus facts nobody has to
  remember to write: branch, uncommitted files, last commits, latest
  decisions. Those are refreshed whenever the MCP host closes.
- **What a tool already learned on its own.** `cairn memory import-native`
  reads Claude Code's per-project auto-memory or Cursor rules and offers them
  as ordinary Cairn decisions, so a fact one tool picked up before Cairn was
  even in the loop still reaches the rest. Gemini CLI's own memory is free-form
  markdown the model edits itself, so it is not a source.

`cairn doctor` shows what is wired up in the current project and what is off.

---

## Argue with your past self

The differentiator: Cairn records decisions with reasoning, alternatives,
evidence, and a supersedes chain, so your agent can recall not just *what*
you decided, but *why*, and what else you considered.

```
You:    The JWT cookie decision: was that final?
Claude: [calls memory_thread("JWT") → finds chain ending at id 67]
        [calls memory_get(67) → reads reasoning, alternatives, evidence]
        "Yes, decision #67, recorded 2026-06-13. We chose httpOnly cookie
         over localStorage to close the XSS token-exfiltration vector.
         The prior approach (#42, localStorage) was superseded for that
         reason. Would you like to revisit it?"

You:    What's changed since then?
Claude: [calls memory_thread(67) → no newer entries]
        "Nothing, #67 is still the active decision."
```

Open the dashboard to see the full provenance graph in your browser:

```bash
cairn memory dashboard --open
```

Single self-contained HTML file. No server, no telemetry. A force-directed
decision graph (nodes = decisions, arrows = supersedes chains, color =
audit-score completeness, zoom/pan controls, double-click to pin a node in
place), with a search box, status filters, and a card-grid list view a
click away. When global memory has entries, a "This Project" / "Global"
tab switches which scope the graph and cards show, so the two never render
on the same canvas at once. A project switcher in the header links out to
other local Cairn projects that have generated their own dashboard.

![Argue with past self](docs/demos/argue-with-past-self.gif)

## Don't repeat a mistake you've already made

`check_approach` checks whether something like the approach on the table was
already tried and rejected, or tried and abandoned:

```
You:    Should we cache this in localStorage?
Claude: [calls check_approach("cache the auth token in localStorage")]
        "That was rejected in decision #42: XSS token-exfiltration risk.
         We went with httpOnly cookies instead (#67). Same concern would
         apply here."
```

Read-only, no LLM call, works the same for project-scoped and
`scope="global"` rejections: a convention you rejected in one project
surfaces here too.

---

## What Cairn brings together

- **Local**: SQLite on your disk. No cloud account, no API key required for storage.
- **MCP-native**: the primary integration surface is the Model Context Protocol, not a bolted-on adapter.
- **Decay-aware**: older entries lose ranking weight over time; recent, relevant memory surfaces first.
- **Temporal validity**: invalidated and superseded decisions stay in the record, marked as such, not silently overwritten.
- **Decision provenance**: first-class records of *why* something was decided, what alternatives were rejected, and what superseded what.
- **IDE-agnostic**: works with any MCP host; no vendor lock-in.
- **MIT-licensed**: free forever, no upsell tier, no hosted version.

---

## What's inside a `.cairn/` folder

```
my-project/
├── src/
└── .cairn/
    ├── config.yaml             # decay, dedup, helper model
    ├── SYSTEM.md               # your project context (hand-written)
    ├── decisions/              # one .md per decision: commit these, review them in PRs
    └── persistence/
        ├── memory.db           # SQLite index (gitignored, rebuildable: cairn memory reindex)
        ├── state.json          # current task / status / handoff pointer
        ├── session-handoff.md  # "where I left off", for the next session in any tool
        ├── context-manifest.md # ~500-token project summary
        └── memory/             # long-form facts / decisions

AGENTS.md                       # your file; Cairn manages only its <!-- cairn:begin/end --> block

~/.cairn/
├── global.db                  # cross-project memory: lives outside any repo
└── projects.json              # registry of local Cairn projects, for the dashboard's project switcher
```

Everything is markdown, SQLite, or JSON. `cat` it, `git diff` it, ship it.

`~/.cairn/global.db` is the one memory file that lives outside a project:
facts and preferences you store with `scope: "global"` (e.g. "I prefer
TypeScript") surface in every project's recall/search, not just the one
where you said it. See `memory.global_enabled` / `memory.global_db_path`
in `config.yaml` to disable or relocate it.

`~/.cairn/projects.json` never holds memory data, it just tracks which
local directories have run `cairn init` or generated a dashboard, so
`cairn memory dashboard` can list and link to sibling projects on the same
machine. See `memory.registry_path` to relocate it.

---

## Three ways to use it

You don't have to pick one: they compose.

| Tier | How it works | IDE-side setup |
|---|---|---|
| **AGENTS.md** | Every active decision, in full, in a managed block of `AGENTS.md`, rewritten on every change. Read at session start by Antigravity, Cursor, Codex and Copilot; by Claude Code when the project has no CLAUDE.md (if it has one, Cairn adds `@AGENTS.md` to it); by Gemini CLI once Cairn adds AGENTS.md to its `context.fileName` (automatic once the project shows you use Gemini CLI). | None: `cairn init` / `cairn sync` |
| **CLI helpers** | `cairn decide`, `cairn handoff`, `cairn memory check "<approach>"`: the same as the MCP tools, for agents without MCP or for you. | None |
| **MCP server** | `cairn mcp serve` over stdio. The IDE calls `memory_recall`, `record_decision`, `decisions_for_path` as tools. | Claude Code and Gemini CLI: `cairn init` offers to register it. Others: one config block, JSON or TOML depending on host (see `cairn mcp config`) |
| **Hooks (optional)** | Claude Code: reminds the agent of a file's decisions right before it edits the file, once per decision per session, and flags a decision you state in passing ("from now on we use X") so it gets recorded. Gemini CLI: the second one only. | Registered by `cairn init` together with the MCP server, or by hand: `cairn mcp config --host claude-code --with-hooks` (or `--host gemini-cli`) |

AGENTS.md works in every agent; the others add to them. Nothing in the
block is truncated. It is a pure function of `.cairn/decisions/`, so it is
identical on every machine; `cairn check` fails CI if it drifts. The cost
grows with the number of active decisions, like any always-loaded file;
Cairn keeps it to the active ones.

> Cairn does not choose models for your IDE. The IDE chooses. Cairn gives
> it the memory and the prompts; the IDE does the thinking.

---

## Decay actually helps recall

`scripts/benchmark_decay.py` seeds 15 adversarial scenarios: for each topic
an older "stale" decision (higher raw importance) competes with a newer
"fresh" decision (lower importance, buried under noise so the sliding
window can't rescue it). Metric: `recall@5`: fresh must rank above stale
in top-5.

| Configuration | recall@5 |
|---|---|
| Decay **off** | **0 %** (raw importance wins → stale always recalled) |
| Decay **on**  | **100 %** (time-weighted score promotes fresh)         |
| Delta         | **+100 pp**                                            |

Synthetic, single-author seed, not LoCoMo, but it pins down the failure
mode decay was built to fix. Run it: `python scripts/benchmark_decay.py`.

A separate, external benchmark against real LoCoMo + LongMemEval-S subsets
(evidence-recall@10, both backends, exact protocol published) lives in
[BENCHMARKS.md](BENCHMARKS.md#external-benchmark--locomo--longmemeval-evidence-recallk),
including where Cairn's default backend currently falls short.

---

## Secrets get redacted, not trusted

Before anything is written (`memory_store`, `record_decision`, any scope),
Cairn scans for common secret formats (API keys, GitHub/AWS/Slack tokens,
PEM private key blocks, generic `password=`/`token=` fields) and masks them.
This is **best-effort pattern matching, not a guarantee**: treat it as a
safety net, not a reason to paste secrets into a conversation on purpose.
Disable with `memory.redact.enabled: false`, or add project-specific patterns
via `memory.redact.extra_patterns`.

## Conflict detection (opt-in)

Set `memory.conflict.enabled: true` and Cairn asks your configured helper
model whether a new decision contradicts a prior high-importance one, in
either DB, project or global. It only ever **warns** (a `conflicts` field on
`record_decision`'s response); it never auto-invalidates anything, because
false positives are common enough that a silent auto-invalidate would erode
trust faster than it saves time. Off by default: it costs a helper-LLM call
per decision, and users who run with `memory_helper_model: null` get nothing
from it either way.

---

## What Cairn is **not**

- Not an agent framework. No internal LLM router, no provider selection, no subagent orchestration.
- Not a SaaS. There is no `cairn.cloud`. There will not be one.
- Not React/Electron-shaped. The dashboard (Phase 2) is a single self-contained HTML file. No Node toolchain.
- Not IDE-locked. No Claude-Code-only features.

If you want any of those, your IDE already has it. Cairn feeds it memory.

---

## CLI reference

```bash
# Initialize
cairn init                              # create .cairn/ and the AGENTS.md decisions block (no helper model)
cairn init --profile cloud              # same, with the optional helper model on Groq
cairn init --profile local              # same, with the optional helper model on a local Ollama server
cairn init --yes                        # also register detected hosts without asking
cairn init --dry-run                    # list every file init would create or change, write nothing
cairn doctor                            # what is wired up here, and what is off
cairn uninstall                         # list what Cairn wired into this project; --yes removes it (keeps .cairn/)

# MCP server (what your IDE calls)
cairn mcp serve                         # stdio JSON-RPC 2.0, Claude Code-compatible
cairn mcp config --host claude-code     # print client config snippet
cairn mcp config --host claude-code --with-hooks  # + opt-in SessionStart + PreToolUse hook recipe (doc-only)
cairn mcp config --host gemini-cli --with-hooks   # + opt-in BeforeAgent decision-detector recipe (doc-only)

# Decisions and handoff (same as the MCP tools, for any shell)
cairn decide "Money is integer cents" --why "float rounding" --rejected float --because rounding --path shop
cairn decide "No tenacity, hand-rolled backoff" --forbid "^\s*(import|from) tenacity\b"  # edits adding it are blocked
cairn handoff "Added the price field" --next "Write apply_discount in shop/pricing.py"
cairn sync                              # rewrite the AGENTS.md block (also automatic on every decision change)
cairn check                             # exit 1 if the AGENTS.md block is missing or out of date (for CI)

# Memory
cairn memory boot                       # session boot context
cairn memory recent --n 10              # last 10 entries
cairn memory search "jwt"               # substring search (auto-includes global.db matches)
cairn memory search "jwt" --path apps/api  # scope results to a monorepo subdirectory
cairn memory invalidate 42 --reason "schema migrated"
cairn memory invalidate g7 --reason "changed my mind"  # 'g<n>' targets a global entry
cairn memory manifest                   # generate context-manifest.md (uses helper model)
cairn memory status                     # current task + handoff
cairn memory dashboard --open           # single-file HTML decision dashboard
cairn memory check "<approach>"         # has this already been rejected or abandoned?
cairn memory for-diff main..HEAD        # which decisions apply to the files this diff changes (no arg: uncommitted work)
cairn memory guard --staged             # exit 1 if staged changes add a line a decision forbids (pre-commit, CI)
cairn memory import-native --source gemini  # Gemini CLI's GEMINI.md notes as decisions (personal ones go global)
```

A longer reference lives in [docs/CLI.md](docs/CLI.md).

---

## Python API

```python
from cairn import Project, Memory

m = Memory({"memory": {"db_path": ".cairn/persistence/memory.db"}})
m.store("decision", "use httpOnly cookies for jwt", importance=2)
m.store("preference", "always use TypeScript", scope="global")  # every project sees this
print(m.recall("jwt cookies"))

p = Project(load_session=False)
print(p.memory.check_rejected("cache the token in localStorage"))
p.memory.invalidate(42, reason="superseded")
```

---

## License + contributing

MIT. Contributing guide: [CONTRIBUTING.md](CONTRIBUTING.md). Security
policy: [SECURITY.md](SECURITY.md).
