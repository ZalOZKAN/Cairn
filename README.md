<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/cover-dark.svg">
    <img src="docs/cover-light.svg" alt="A cairn of 37 stones, one for each commit. A moonbeam lights the one stone that matters while sparks of memory drift in the dark." width="100%">
  </picture>
</p>

<p align="center"><em>Every stone is a decision. The sparks are everything else an agent could remember.<br>
Cairn's job is to find the one stone that matters for the file in front of you, and light it.</em></p>

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

> **Status: experimental.** Cairn is not on PyPI yet. Install it from this repository
> (below); the measurements behind the claims are in [BENCHMARKS.md](BENCHMARKS.md).

## 30-second install

```bash
pip install git+https://github.com/ZalOZKAN/Cairn
cd ~/my-project
cairn init                             # also adds a decisions block to AGENTS.md
```

On Windows, if `cairn` is blocked (Smart App Control refuses the unsigned
`cairn.exe` launcher that pip creates), run it through Python instead:
`python -m cairn init`.

`cairn init` alone already gives every agent your decisions: they are kept in a
managed block of `AGENTS.md`, which Antigravity, Cursor, Codex and Copilot read
on their own. Claude Code (2.1.277 or later) reads it when the project has no
`CLAUDE.md`, and through an `@AGENTS.md` import Cairn adds when it has one;
Gemini CLI reads it once Cairn adds the file to its settings. If it finds Claude Code or Gemini CLI it offers to register the MCP
server and hooks for this project, so the agent gets the memory tools after a
restart; any other host gets a one-line pointer to `cairn mcp config --host
<name>`. Details are in [the guide](docs/GUIDE.md#setting-up-a-project).

Then record a decision, and ask whether an idea was already rejected:

```bash
cairn decide "Money is integer cents" --why "float rounding errors" \
  --rejected "floating point numbers" --because "rounding errors" --path shop
cairn memory check "store prices as floating point numbers"
cairn memory dashboard --open          # every decision, and what replaced what
```

---

## Argue with your past self

Cairn records decisions with reasoning, rejected alternatives, evidence and a
supersedes chain, so your agent can recall not just *what* you decided, but
*why*, and what else you considered.

A typical exchange (illustrative):

```
You:    The JWT cookie decision: was that final?
Claude: [calls memory_thread("JWT") → finds chain ending at id 67]
        [calls memory_get(67) → reads reasoning, alternatives, evidence]
        "Yes, decision #67, recorded 2026-06-13. We chose httpOnly cookie
         over localStorage to close the XSS token-exfiltration vector.
         The prior approach (#42, localStorage) was superseded for that
         reason. Would you like to revisit it?"
```

It also checks before you repeat a mistake: `check_approach` tells the agent
that something like the approach on the table was already rejected, and why.

## What Cairn brings together

- **Local**: SQLite on your disk. No cloud account, no API key required for storage.
- **MCP-native**: the primary integration surface is the Model Context Protocol, not a bolted-on adapter.
- **Decay-aware**: older entries lose ranking weight over time; recent, relevant memory surfaces first.
- **Temporal validity**: invalidated and superseded decisions stay in the record, marked as such, not silently overwritten.
- **Decision provenance**: first-class records of *why* something was decided, what alternatives were rejected, and what superseded what.
- **IDE-agnostic**: works with any MCP host; no vendor lock-in.
- **MIT-licensed**: free forever, no upsell tier, no hosted version.

## Switch tools, keep the memory

Start in Antigravity, continue in Claude Code, finish in Cursor. Every active
decision is in `AGENTS.md` whichever tool recorded it, the last agent's note
(what was done, the next step) sits in `.cairn/persistence/session-handoff.md`,
and everything is plain text in your repo, so another AI can pick up where one
ran out of quota. See [Switch tools](docs/GUIDE.md#switch-tools-keep-the-memory)
and [Out of quota](docs/GUIDE.md#out-of-quota-continue-in-another-ai).

## When a plain markdown file is enough

If you work alone, in one tool, with a few dozen decisions, and you tell the
agent to keep a decisions or log file, that already works: one file can hold
"we tried X and rejected it", and one file can carry memory between tools.
Cairn does not claim otherwise. It is for what such a file cannot do:

- **Enforce, not ask.** A decision can forbid a pattern, and a hook blocks the edit that adds it, whether or not the
  model remembers the rule.
- **Scale.** Only the decisions for the file being edited are delivered, instead of the whole file every session.
- **Many writers.** One file per decision merges cleanly in git; one shared file conflicts and drifts.
- **Safety.** A decision cannot reach every agent as an instruction without being checked first.
- **Audit.** You can see what Cairn caught and what it missed.

How much each of these is worth on your kind of project is being measured, not
assumed.

**What it saves, and where.** Against a flat markdown file of decisions, delivering only the decisions that apply to the
file being edited cut total tokens by about two thirds at 1,000 decisions in our benchmark (Claude Code, one model, short tasks). The saving grows
with the store. In a small store (a few hundred decisions or fewer) the flat file is already small, so no saving is claimed
there. The numbers and their limits are in [BENCHMARKS.md](BENCHMARKS.md) and [the guide](docs/GUIDE.md#three-ways-to-use-it).

## Learn more

| Read | For |
|---|---|
| [docs/GUIDE.md](docs/GUIDE.md) | Day-to-day use: setup, switching tools, handoff, the three ways to use it, measuring it on your own sessions, languages, conflict detection |
| [docs/CLI.md](docs/CLI.md) | Every command, with a cheat sheet at the top |
| [docs/FORMAT.md](docs/FORMAT.md) | What is inside a `.cairn/` folder, and the Python API |
| [docs/SECURITY-MODEL.md](docs/SECURITY-MODEL.md) | How secrets are redacted and why decisions are treated as data, not commands |
| [INTEGRATIONS.md](INTEGRATIONS.md) | Per-host notes: Claude Code, Cursor, Codex CLI, Gemini CLI, Continue.dev, Windsurf, Zed and any generic MCP host |
| [BENCHMARKS.md](BENCHMARKS.md) | What was measured, how, and where Cairn falls short |
| [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md) | Contributing, and reporting a vulnerability |

## What Cairn is **not**

- Not an agent framework. No internal LLM router, no provider selection, no subagent orchestration.
- Not a SaaS. There is no `cairn.cloud`. There will not be one.
- Not React/Electron-shaped. The dashboard is a single self-contained HTML file. No Node toolchain.
- Not IDE-locked. Everything works through `AGENTS.md` and MCP; host-specific extras (Claude Code hooks and scoped rule files) are optional additions, never required.

If you want any of those, your IDE already has it. Cairn feeds it memory.

## License + contributing

MIT. Contributing guide: [CONTRIBUTING.md](CONTRIBUTING.md). Security
policy: [SECURITY.md](SECURITY.md).
