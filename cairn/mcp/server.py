"""
Cairn MCP server, exposes a project's `.cairn/` folder as
Model Context Protocol tools.

Any MCP host (Claude Code, Cursor, Antigravity, custom CLI) can connect
to this server over stdio and gain read/write access to the project's
persistent memory, skills, agents, and workflows.

Run from a project directory:

    cairn mcp serve

Claude Code config (`~/.claude/claude_desktop_config.json`):

    {
      "mcpServers": {
        "cairn": {
          "command": "gs",
          "args": ["mcp", "serve"]
        }
      }
    }

Tools exposed:
    memory_recall      , semantic + keyword recall, decay-aware
    memory_store       , write a new memory entry
    memory_recent      , last N entries
    memory_search      , substring search
    memory_invalidate  , retire a memory by id with optional reason
    memory_boot        , session boot context
    memory_status      , current task + last handoff
    record_decision    , first-class decision with reasoning/alternatives/evidence/supersedes
    memory_get         , full record including evidence + supersedes chain
    memory_thread      , chronological versions of a decision topic
    decision_audit_score, completeness score 0/4..4/4 for a decision
    list_skills        , enumerate available skills
    get_skill          , return a skill's system-prompt markdown
    list_workflows     , enumerate workflows
    get_workflow       , return a workflow's markdown
    list_agents        , enumerate agent role MDs
    get_agent          , return an agent role markdown
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

from cairn import __version__


class CairnMCPServer:
    """Minimal MCP server, JSON-RPC 2.0 over stdio."""

    def __init__(self, project_dir: Path | None = None):
        self.project_dir = project_dir or Path.cwd()
        self._project = None  # lazy
        self._tools = self._define_tools()
        # auto-save state (Phase 2 Stream A)
        self._store_count = 0
        self._last_save_at = 0.0
        self._idle_timer: threading.Timer | None = None
        self._autosave_lock = threading.Lock()

    # ---- project (lazy) -------------------------------------------------

    @property
    def project(self):
        if self._project is None:
            from cairn.core import Project
            self._project = Project(project_dir=self.project_dir, load_session=False)
        return self._project

    # ---- main loop ------------------------------------------------------

    def run(self) -> None:
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    response = self._handle(request)
                except Exception as exc:  # surface protocol error
                    response = {
                        "jsonrpc": "2.0",
                        "id": request.get("id"),
                        "error": {"code": -32603, "message": str(exc)},
                    }
                if response:
                    self._send(response)
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        """Trigger #5: final flush + extract on EOF/SIGTERM."""
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        if self._project is None:
            return
        try:
            self._project.memory.flush()
            self._maybe_autosave(force=True, reason="shutdown")
            self._project.memory.close()
        except Exception:
            pass

    # ---- auto-save (Phase 2 Stream A) -----------------------------------

    def _autosave_cfg(self) -> dict:
        return (self.project.config.get("memory", {}) or {}).get("auto_save", {}) or {}

    def _on_store(self) -> None:
        """Called after every successful memory_store. Handles triggers #2 and #4."""
        cfg = self._autosave_cfg()
        if not cfg.get("enabled", True):
            return
        self._store_count += 1
        every = int(cfg.get("every_n_stores", 30))
        cooldown = float(cfg.get("cooldown_minutes", 10)) * 60.0
        now = time.monotonic()
        if every > 0 and self._store_count % every == 0 and (now - self._last_save_at) >= cooldown:
            self._spawn_autosave("threshold")
        idle_seconds = float(cfg.get("idle_minutes", 10)) * 60.0
        if idle_seconds > 0:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
            t = threading.Timer(idle_seconds, self._spawn_autosave, args=("idle",))
            t.daemon = True
            t.start()
            self._idle_timer = t

    def _spawn_autosave(self, reason: str) -> None:
        t = threading.Thread(
            target=self._maybe_autosave,
            kwargs={"reason": reason},
            name=f"cairn-autosave-{reason}",
            daemon=True,
        )
        t.start()

    def _maybe_autosave(self, reason: str, force: bool = False) -> dict | None:
        """Run extractor over recent session entries. Returns extraction dict or None."""
        if not self._autosave_lock.acquire(blocking=False):
            return None
        try:
            cfg = self._autosave_cfg()
            if not force and not cfg.get("enabled", True):
                return None
            try:
                from cairn.memory.extractor import MemoryExtractor
                extractor = MemoryExtractor(self.project)
            except Exception:
                return None
            recent = self.project.memory.recent(n=30)
            if not recent:
                return None
            entries = [
                {"prompt": r["prompt"], "response": r["response"], "timestamp": r["timestamp"]}
                for r in recent
            ]
            try:
                extraction = extractor.extract_from_session(entries)
            except Exception:
                return None
            if cfg.get("skip_if_helper_unavailable", True) and extraction.get("_error"):
                return None
            self._last_save_at = time.monotonic()
            return extraction
        finally:
            self._autosave_lock.release()

    def _handle(self, request: dict) -> dict | None:
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {}) or {}

        if method == "initialize":
            return self._respond(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "cairn", "version": __version__},
            })
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return self._respond(req_id, {"tools": self._tools})
        if method == "tools/call":
            name = params.get("name", "")
            args = params.get("arguments", {}) or {}
            text = self._call_tool(name, args)
            return self._respond(req_id, {"content": [{"type": "text", "text": text}]})

        return self._respond(req_id, {}) if req_id is not None else None

    # ---- tool dispatch --------------------------------------------------

    def _call_tool(self, name: str, args: dict) -> str:
        handler = _TOOL_HANDLERS.get(name)
        if handler is None:
            return f"Unknown tool: {name}"
        try:
            return handler(self, args)
        except Exception as exc:
            return f"Error in {name}: {exc}"

    # ---- json helpers --------------------------------------------------

    @staticmethod
    def _respond(req_id, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    @staticmethod
    def _send(data: dict) -> None:
        print(json.dumps(data), flush=True)

    # ---- tool catalogue ------------------------------------------------

    def _define_tools(self) -> list[dict]:
        return [
            _tool("memory_recall",
                  "Decay-aware recall of past memory entries relevant to a query.",
                  {"query": _str("Search query"),
                   "limit": _int("Max entries to return", default=5)},
                  required=["query"]),
            _tool("memory_store",
                  "Add a new memory entry. Importance 1-3 controls ranking weight.",
                  {"prompt": _str("User-side prompt / topic"),
                   "response": _str("Assistant response / fact to remember"),
                   "importance": _int("1=normal, 2=decision, 3=critical", default=1)},
                  required=["prompt", "response"]),
            _tool("memory_recent",
                  "Return the N most recent valid memory entries.",
                  {"n": _int("How many", default=10)}),
            _tool("memory_search",
                  "Substring search across memory.",
                  {"query": _str("Substring"),
                   "limit": _int("Max results", default=10)},
                  required=["query"]),
            _tool("memory_invalidate",
                  "Retire a memory entry by id without deleting it.",
                  {"id": _int("Memory row id"),
                   "reason": _str("Why it's no longer valid", default="")},
                  required=["id"]),
            _tool("memory_boot",
                  "Build a session boot context, what to load at session start.",
                  {"format": {"type": "string", "enum": ["text", "claude", "json"],
                              "description": "Output format (default text)"},
                   "include_memory": {"type": "array", "items": {"type": "string"},
                                      "description": "Extra memory file names to include"}}),
            _tool("memory_status",
                  "Current task, status, and last handoff for this project.",
                  {}),
            _tool("list_skills",
                  "List available skills (built-in + project-local).", {}),
            _tool("get_skill",
                  "Return a skill's markdown system prompt.",
                  {"alias_or_name": _str("e.g. @cr or code-review")},
                  required=["alias_or_name"]),
            _tool("list_workflows", "List available workflows.", {}),
            _tool("get_workflow",
                  "Return a workflow's markdown system prompt.",
                  {"name": _str("e.g. /plan or plan")},
                  required=["name"]),
            _tool("list_agents", "List available agent role MDs.", {}),
            _tool("get_agent",
                  "Return an agent role markdown.",
                  {"name": _str("e.g. adversary")},
                  required=["name"]),
            _tool("record_decision",
                  "Record a first-class decision. If this is a decision, please also pass "
                  "reasoning and at least one alternative so future-you can audit the choice.",
                  {"decision": _str("The decision in one sentence"),
                   "reasoning": _str("Why we chose this", default=""),
                   "alternatives": {"type": "array",
                                    "description": "Rejected options",
                                    "items": {"type": "object", "properties": {
                                        "option": {"type": "string"},
                                        "rejected_because": {"type": "string"}}}},
                   "evidence": {"type": "array",
                                "description": "Supporting sources/claims",
                                "items": {"type": "object", "properties": {
                                    "source": {"type": "string"},
                                    "claim": {"type": "string"}}}},
                   "supersedes": _int("Prior decision id this replaces"),
                   "chose_because": _str("Short tag for the deciding factor", default=""),
                   "importance": _int("1=normal, 2=decision, 3=critical", default=2)},
                  required=["decision"]),
            _tool("memory_get",
                  "Full record for a memory id: reasoning, alternatives, evidence, supersedes chain.",
                  {"id": _int("Memory row id")},
                  required=["id"]),
            _tool("memory_thread",
                  "Chronological versions of a decision topic. Pass an id to walk the "
                  "supersedes chain, or a string for keyword-overlap grouping.",
                  {"topic_or_id": _str("Topic keyword OR a numeric id as string"),
                   "limit": _int("Max entries", default=20)},
                  required=["topic_or_id"]),
            _tool("decision_audit_score",
                  "Completeness score 0/4..4/4 for a decision: reasoning, alternatives, evidence, supersedes.",
                  {"id": _int("Decision row id")},
                  required=["id"]),
            _tool("memory_save",
                  "Run the helper-LLM extractor over recent session entries. Hosts should "
                  "call this at session boundaries (handoff, before exit) so structured "
                  "memory survives the next session.",
                  {}),
        ]


# ---- tool handlers --------------------------------------------------

def _h_recall(srv: CairnMCPServer, args: dict) -> str:
    out = srv.project.memory.recall(args.get("query", ""), limit=int(args.get("limit", 5)))
    return out or "(no relevant memory)"


def _h_store(srv: CairnMCPServer, args: dict) -> str:
    srv.project.memory.store(
        args.get("prompt", ""),
        args.get("response", ""),
        importance=int(args.get("importance", 1)),
    )
    srv.project.memory.flush()
    srv._on_store()
    return "stored"


def _h_memory_save(srv: CairnMCPServer, args: dict) -> str:
    """Trigger #1: explicit MCP memory_save tool. Hosts call at session boundaries."""
    extraction = srv._maybe_autosave(reason="explicit", force=True)
    if extraction is None:
        return "no extraction (helper unavailable or no recent entries)"
    return json.dumps({
        "ok": True,
        "facts": len(extraction.get("facts", [])),
        "decisions": len(extraction.get("decisions", [])),
        "tasks_completed": len(extraction.get("tasks_completed", [])),
        "tasks_in_progress": len(extraction.get("tasks_in_progress", [])),
    }, ensure_ascii=False)


def _h_recent(srv: CairnMCPServer, args: dict) -> str:
    rows = srv.project.memory.recent(n=int(args.get("n", 10)))
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _h_search(srv: CairnMCPServer, args: dict) -> str:
    rows = srv.project.memory.search(args.get("query", ""), limit=int(args.get("limit", 10)))
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _h_invalidate(srv: CairnMCPServer, args: dict) -> str:
    ok = srv.project.memory.invalidate(int(args["id"]), reason=args.get("reason", ""))
    return "invalidated" if ok else "not found or already invalid"


def _h_boot(srv: CairnMCPServer, args: dict) -> str:
    fmt = args.get("format", "text")
    extras = args.get("include_memory") or []
    return srv.project.injector.build(fmt=fmt, include_memory=extras or None) or "(empty)"


def _h_status(srv: CairnMCPServer, _args: dict) -> str:
    state = srv.project.session.load_state()
    handoff = srv.project.session.load_handoff()
    out = [
        f"task: {state.get('current_task') or 'none'}",
        f"status: {state.get('task_status', 'idle')}",
        f"session #: {state.get('session_count', 0)}",
    ]
    if handoff:
        out.append("\nlast handoff:\n" + handoff[:500])
    return "\n".join(out)


def _h_list_skills(srv: CairnMCPServer, _args: dict) -> str:
    return json.dumps(srv.project.list_skills(), ensure_ascii=False, indent=2)


def _h_get_skill(srv: CairnMCPServer, args: dict) -> str:
    return srv.project.get_skill(args["alias_or_name"]) or "(not found)"


def _h_list_workflows(srv: CairnMCPServer, _args: dict) -> str:
    return json.dumps(srv.project.list_workflows(), ensure_ascii=False, indent=2)


def _h_get_workflow(srv: CairnMCPServer, args: dict) -> str:
    return srv.project.get_workflow(args["name"]) or "(not found)"


def _h_list_agents(srv: CairnMCPServer, _args: dict) -> str:
    return json.dumps(srv.project.list_agents(), ensure_ascii=False, indent=2)


def _h_get_agent(srv: CairnMCPServer, args: dict) -> str:
    return srv.project.get_agent(args["name"]) or "(not found)"


def _h_record_decision(srv: CairnMCPServer, args: dict) -> str:
    new_id = srv.project.memory.record_decision(
        decision=args["decision"],
        reasoning=args.get("reasoning") or None,
        alternatives=args.get("alternatives") or None,
        evidence=args.get("evidence") or None,
        supersedes=int(args["supersedes"]) if args.get("supersedes") is not None else None,
        chose_because=args.get("chose_because") or None,
        importance=int(args.get("importance", 2)),
    )
    return json.dumps({"id": new_id, "kind": "decision"}, ensure_ascii=False)


def _h_memory_get(srv: CairnMCPServer, args: dict) -> str:
    full = srv.project.memory.get_full(int(args["id"]))
    return json.dumps(full, ensure_ascii=False, indent=2) if full else "(not found)"


def _h_memory_thread(srv: CairnMCPServer, args: dict) -> str:
    raw = args["topic_or_id"]
    target: int | str
    try:
        target = int(raw)
    except (ValueError, TypeError):
        target = str(raw)
    rows = srv.project.memory.thread(target, limit=int(args.get("limit", 20)))
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _h_decision_audit_score(srv: CairnMCPServer, args: dict) -> str:
    return json.dumps(
        srv.project.memory.decision_audit_score(int(args["id"])),
        ensure_ascii=False, indent=2,
    )


_TOOL_HANDLERS = {
    "memory_recall": _h_recall,
    "memory_store": _h_store,
    "memory_recent": _h_recent,
    "memory_search": _h_search,
    "memory_invalidate": _h_invalidate,
    "memory_boot": _h_boot,
    "memory_status": _h_status,
    "list_skills": _h_list_skills,
    "get_skill": _h_get_skill,
    "list_workflows": _h_list_workflows,
    "get_workflow": _h_get_workflow,
    "list_agents": _h_list_agents,
    "get_agent": _h_get_agent,
    "record_decision": _h_record_decision,
    "memory_get": _h_memory_get,
    "memory_thread": _h_memory_thread,
    "decision_audit_score": _h_decision_audit_score,
    "memory_save": _h_memory_save,
}


# ---- schema helpers ---------------------------------------------------

def _str(description: str, default: Any = None) -> dict:
    s = {"type": "string", "description": description}
    if default is not None:
        s["default"] = default
    return s


def _int(description: str, default: Any = None) -> dict:
    s = {"type": "integer", "description": description}
    if default is not None:
        s["default"] = default
    return s


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return {"name": name, "description": description, "inputSchema": schema}
