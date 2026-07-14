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
> was made. You ask "should we use localStorage for the new auth flow?" -
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
cairn mcp config --host claude-code   # (or cursor, antigravity, generic)
```

Paste the printed JSON into your IDE's MCP config, restart, and the agent
gets eighteen memory tools, `memory_recall`, `memory_store`, `memory_invalidate`,
`record_decision`, `memory_thread`, `decision_audit_score`, `get_skill`, and
friends, without you writing a line of glue code.

![Claude Code demo](docs/demos/claude-code.gif)

---

## Argue with your past self

The differentiator: Cairn records decisions with reasoning, alternatives,
evidence, and a supersedes chain, so your agent can recall not just *what*
you decided, but *why*, and what else you considered.

```
You:    The JWT cookie decision, was that final?
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

Single self-contained HTML file. No server, no telemetry, just card grid,
timeline, and audit-score badges showing how complete each decision's record is.

![Argue with past self](docs/demos/argue-with-past-self.gif)

---

## What's inside a `.cairn/` folder

```
my-project/
├── src/
└── .cairn/
    ├── config.yaml             # decay, dedup, helper model
    ├── SYSTEM.md               # your project context (hand-written)
    ├── persistence/
    │   ├── memory.db           # SQLite: decay-aware recall, temporal validity
    │   ├── state.json          # current task / status / handoff pointer
    │   ├── session-handoff.md  # "where I left off"
    │   ├── context-manifest.md # ~500-token project summary
    │   └── memory/             # long-form facts / decisions
    ├── skills/                 # project-local skill overrides
    ├── workflows/              # project-local pipelines
    └── agents/                 # project-local role personas
```

Everything is markdown or SQLite. `cat` it, `git diff` it, ship it.

---

## Three ways to use it

You don't have to pick one, they compose.

| Tier | How it works | IDE-side setup |
|---|---|---|
| **File format** | Reference `.cairn/SYSTEM.md` or a skill MD in any IDE that lets you attach files. | None |
| **CLI helpers** | `cairn memory boot --copy` → clipboard. `cairn skill show @cr` → print. Pipe into any tool. | None |
| **MCP server** | `cairn mcp serve` over stdio. The IDE calls `memory_recall`, `memory_store`, `get_skill` as tools. | One JSON block (see `cairn mcp config`) |

The first two require zero IDE support. The third needs one config block
per host, Cairn itself stays a single binary.

> Cairn does not choose models for your IDE. The IDE chooses. Cairn gives
> it the memory and the prompts; the IDE does the thinking.

---

## Decay actually helps recall

`scripts/benchmark_decay.py` seeds 15 adversarial scenarios: for each topic
an older "stale" decision (higher raw importance) competes with a newer
"fresh" decision (lower importance, buried under noise so the sliding
window can't rescue it). Metric: `recall@5`, fresh must rank above stale
in top-5.

| Configuration | recall@5 |
|---|---|
| Decay **off** | **0 %** (raw importance wins → stale always recalled) |
| Decay **on**  | **100 %** (time-weighted score promotes fresh)         |
| Delta         | **+100 pp**                                            |

Synthetic, single-author seed, not LoCoMo, but it pins down the failure
mode decay was built to fix. Run it: `python scripts/benchmark_decay.py`.

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

# Memory
cairn memory boot                       # session boot context
cairn memory recent --n 10              # last 10 entries
cairn memory search "jwt"               # substring search
cairn memory invalidate 42 --reason "schema migrated"
cairn memory manifest                   # generate context-manifest.md (uses helper model)
cairn memory status                     # current task + handoff
cairn memory dashboard --open           # single-file HTML decision dashboard

# Skills (read-only)
cairn skills                            # list available skills
cairn skill show @cr                    # print code-review skill prompt
cairn skill path @cr                    # print on-disk path

# Workflows (read-only)
cairn workflow list
cairn workflow show /plan
```

A longer reference lives in [docs/CLI.md](file:///c:/Projects/Cairn/docs/CLI.md).

---

## Python API

```python
from cairn import Project, Memory

m = Memory({"memory": {"db_path": ".cairn/persistence/memory.db"}})
m.store("decision", "use httpOnly cookies for jwt", importance=2)
print(m.recall("jwt cookies"))

p = Project()
print(p.list_skills())
p.memory.invalidate(42, reason="superseded")
```

---

## License + contributing

MIT. Contributing guide: [CONTRIBUTING.md](CONTRIBUTING.md). Security
policy: [SECURITY.md](SECURITY.md).
