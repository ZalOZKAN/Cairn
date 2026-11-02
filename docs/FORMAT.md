# The `.cairn/` folder and the Python API

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
