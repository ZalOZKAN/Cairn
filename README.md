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
cairn init
cairn mcp config --host claude-code   # (or cursor, antigravity, codex, opencode, windsurf, zed, generic)
```

Paste the printed config block into your IDE's MCP config, restart, and the
agent gets fourteen memory tools: `memory_recall`, `memory_store`, `memory_invalidate`,
`record_decision`, `decisions_for_path`, `memory_thread`, `check_approach`,
and friends, without you writing a line of glue code.

![Claude Code demo](docs/demos/claude-code.gif)

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
        ├── session-handoff.md  # "where I left off"
        ├── context-manifest.md # ~500-token project summary
        └── memory/             # long-form facts / decisions

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
| **File format** | Reference `.cairn/SYSTEM.md` or files in `.cairn/decisions/` in any IDE that lets you attach files. | None |
| **CLI helpers** | `cairn memory boot --copy` → clipboard. `cairn memory check "<approach>"` → print. Pipe into any tool. | None |
| **MCP server** | `cairn mcp serve` over stdio. The IDE calls `memory_recall`, `memory_store`, `record_decision` as tools. | One config block, JSON or TOML depending on host (see `cairn mcp config`) |

The first two require zero IDE support. The third needs one config block
per host, Cairn itself stays a single binary.

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
cairn init                              # create .cairn/ in current dir (cloud helper model)
cairn init --profile local              # same, but the helper model runs on a local Ollama server

# MCP server (what your IDE calls)
cairn mcp serve                         # stdio JSON-RPC 2.0, Claude Code-compatible
cairn mcp config --host claude-code     # print client config snippet
cairn mcp config --host claude-code --with-hooks  # + opt-in SessionStart + PreToolUse hook recipe (doc-only)

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
```

A longer reference lives in [docs/CLI.md](file:///c:/Projects/Cairn/docs/CLI.md).

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
