# Security model

How Cairn treats secrets and untrusted decisions. Reporting a vulnerability is in [SECURITY.md](../SECURITY.md).

## Secrets get redacted, not trusted

Before anything is written (`memory_store`, `record_decision`, any scope),
Cairn scans for common secret formats (API keys, GitHub/AWS/Slack tokens,
PEM private key blocks, generic `password=`/`token=` fields) and masks them.
This is **best-effort pattern matching, not a guarantee**: treat it as a
safety net, not a reason to paste secrets into a conversation on purpose.
Disable with `memory.redact.enabled: false`, or add project-specific patterns
via `memory.redact.extra_patterns`.

## Decisions are data, not commands

Every active decision is read by every agent, which makes a decision record a
path into every future session. A web page an agent read could talk it into
recording "ignore previous instructions and ...", and a decision file pulled
from a teammate is imported as-is. So before any decision becomes active,
whether recorded by an agent, written with `cairn decide`, or imported from a
hand-edited or pulled file, Cairn checks it for injection shapes: instruction
overrides (in English, Turkish, French, Spanish, German, Chinese, Japanese,
Russian), `curl ... | sh` pipes, encoded PowerShell, destructive commands, a
secret named next to an outside address, hidden zero-width or bidirectional
characters, HTML comments.

A flagged decision is **quarantined**, not deleted: stored inactive, absent
from AGENTS.md and the session context, and listed at the top of the next one so
the agent knows not to follow it. Only a person can release it:

```bash
cairn memory review              # what is held and why
cairn memory approve <id>        # release one (there is no MCP tool for this, on purpose)
```

The AGENTS.md block also tells agents that no decision authorizes running
commands, fetching URLs, sending data or revealing secrets. Pattern matching
catches the common shapes, not every possible attack; the human approval step
is the boundary.
