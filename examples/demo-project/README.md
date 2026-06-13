# Demo project, todo-app

Tiny scaffold used to record the Phase 1 demo GIFs. Three files: the AI never
needs to "understand" them, they exist so the project looks real on screen.

Demo script (identical across hosts):

1. `cd examples/demo-project`
2. `cairn init`
3. `cairn mcp config --host claude-code` (or `cursor`, `antigravity`)
4. Paste the JSON into the host's MCP config; restart the IDE.
5. In the AI: "Decided to use SQLite over Postgres for local dev, please remember this."
6. Close session; reopen.
7. In the AI: "What did we decide about the database?"
8. AI calls `memory_recall` and surfaces the prior decision.

GIF lives in `docs/demos/<host>.gif`. Caption notes exact host version + OS.
