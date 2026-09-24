"""
Cairn MCP server, exposes a project's `.cairn/` folder as
Model Context Protocol tools.

Any MCP host (Claude Code, Cursor, Antigravity, custom CLI) can connect
to this server over stdio and gain read/write access to the project's
persistent memory.

Run from a project directory:

    cairn mcp serve

Client config: run `cairn mcp config --host <host>` for the exact block and
file path for your host (Claude Code, Cursor, Codex, OpenCode, Windsurf,
Zed, ...), the shape and format genuinely differ per host (Codex is TOML;
Zed uses `context_servers`; OpenCode uses `type: "local"` with a combined
command array), so there is no single snippet to paste here without risking
staleness or a wrong host's syntax.

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
    check_approach     , has this approach already been rejected or abandoned?
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

from cairn import __version__
from cairn.memory.decision_files import format_brief
from cairn.memory.injector import AGENT_GUIDE
from cairn.memory.stats import bump


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
            recent = self.project.memory.recent(n=30)
            if not recent:
                return None
            entries = [
                {"prompt": r["prompt"], "response": r["response"], "timestamp": r["timestamp"]}
                for r in recent
            ]
            extraction = None
            helper_available = False
            
            if self.project.config.get("memory_helper_model"):
                try:
                    from cairn.memory.extractor import MemoryExtractor
                    extractor = MemoryExtractor(self.project)
                    extraction = extractor.extract_from_session(entries)
                    if not (cfg.get("skip_if_helper_unavailable", True) and extraction.get("_error")):
                        helper_available = True
                except Exception as e:
                    import sys
                    print(f"Auto-save extraction error: {e}", file=sys.stderr)
            
            if not helper_available:
                in_progress = [
                    {"task": e["prompt"][:80], "status": "recorded", "next_step": "review"}
                    for e in entries[:5]
                ]
                completed = [e["prompt"][:80] for e in entries[5:15]]
                extraction = {
                    "next_session_start": "Review and continue from the recent topics listed below.",
                    "tasks_in_progress": in_progress,
                    "tasks_completed": completed,
                    "_fallback": True
                }
            
            try:
                self.project.session.end_session(extraction, summary=f"Auto-saved (reason: {reason})")
            except Exception as e:
                import sys
                print(f"Auto-save persist error: {e}", file=sys.stderr)
                
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
                "instructions": AGENT_GUIDE,
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
            text = handler(self, args)
        except Exception as exc:
            text = f"Error in {name}: {exc}"
        hit = not (text.startswith("(no") or text.startswith("Error in"))
        bump(self.project_dir, name, hit=hit)
        return text

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
                   "limit": _int("Max entries to return", default=5),
                   "path": _str("Optional workspace-relative path (e.g. 'apps/api') to scope "
                                 "results to that path and its ancestors; repo-wide entries "
                                 "always match. Omit for unscoped, repo-wide recall.")},
                  required=["query"]),
            _tool("memory_store",
                  "Add a new memory entry. Importance 1-3 controls ranking weight. "
                  "Set scope='global' WITHOUT being asked whenever the user states a "
                  "preference or convention that isn't specific to this codebase, e.g. "
                  "'I always use TypeScript', 'I write commit messages in Conventional "
                  "Commits format', 'I don't like using any'. Anything that would still be "
                  "true in a different repo belongs in global scope so it isn't re-explained "
                  "next project. Leave scope at the 'project' default for facts tied to this "
                  "specific codebase (its stack, its architecture, its bugs).",
                  {"prompt": _str("User-side prompt / topic"),
                   "response": _str("Assistant response / fact to remember"),
                   "importance": _int("1=normal, 2=decision, 3=critical", default=1),
                   "scope_path": _str("Optional workspace-relative path this entry applies to "
                                       "(e.g. 'apps/api'). Omit for repo-wide."),
                   "scope": _str("'project' (default, this project's .cairn/) or 'global' "
                                 "(~/.cairn/global.db, cross-project preferences/facts, "
                                 "surfaces in every project's recall/search). See the tool "
                                 "description above for when to pick 'global' unprompted.",
                                 default="project")},
                  required=["prompt", "response"]),
            _tool("memory_recent",
                  "Return the N most recent valid memory entries.",
                  {"n": _int("How many", default=10)}),
            _tool("memory_search",
                  "Substring search across memory.",
                  {"query": _str("Substring"),
                   "limit": _int("Max results", default=10),
                   "path": _str("Optional workspace-relative path to scope results to "
                                 "(same semantics as memory_recall's path param).")},
                  required=["query"]),
            _tool("memory_invalidate",
                  "Retire a memory entry by id without deleting it.",
                  {"id": _str("Memory row id, a plain number for a project entry, or "
                               "'g<n>' (e.g. 'g12') for a global entry"),
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
            _tool("record_decision",
                  "Record a settled decision so it is not re-argued later. Call it the moment a choice "
                  "between options is settled, by you or the user: a library or tool, an architecture "
                  "or pattern, a data model, an API shape, a convention, a trade-off, or 'we tried X "
                  "and it did not work'. Pass reasoning, the rejected alternatives, and scope_path "
                  "when it only applies to part of the repo. If it replaces an earlier decision, pass "
                  "supersedes. The decision is saved as a reviewable file in .cairn/decisions/. "
                  "If the decision is a standing preference that would apply to any project, not just this codebase (e.g. 'always use pnpm over npm', 'prefer functional components in React'), pass scope='global' unprompted so it isn't re-decided next project. "
                  "If `memory.conflict.enabled` is set in config, the response may include a `conflicts` field flagging prior decisions this one may contradict, treat it as a prompt to ask the user, never as a reason to silently invalidate anything.",
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
                   "supersedes": _str("Prior decision id this replaces, plain number for a "
                                       "project decision, or 'g<n>' for a global decision"),
                   "chose_because": _str("Short tag for the deciding factor", default=""),
                   "importance": _int("1=normal, 2=decision, 3=critical", default=2),
                   "scope_path": _str("Optional workspace-relative path this decision applies "
                                       "to (e.g. 'apps/api'). Omit for repo-wide. Only meaningful "
                                       "for scope='project'."),
                   "scope": _str("'project' (default, this project's .cairn/) or 'global' "
                                 "(~/.cairn/global.db, cross-project decision, e.g. a coding "
                                 "convention or preference that applies to every project).",
                                 default="project")},
                  required=["decision"]),
            _tool("check_approach",
                  "Call this BEFORE proposing an architectural or implementation approach, checks "
                  "whether something like it was already tried and rejected (a decision's rejected "
                  "alternative) or abandoned (a decision later invalidated/superseded), in this "
                  "project or globally. Returns the reason it didn't work last time. Empty result "
                  "means no record of it, not a guarantee it's safe, just that Cairn has no memory "
                  "of it being tried.",
                  {"approach": _str("The approach being considered, in a sentence or two"),
                   "limit": _int("Max matches to return", default=5)},
                  required=["approach"]),
            _tool("decisions_for_path",
                  "Call this BEFORE editing or creating a file. Returns the active decisions "
                  "that apply to that file's directory (e.g. 'API handlers use SQLAlchemy core, "
                  "not the ORM'), flagged NEEDS REVIEW when the code has changed a lot since. "
                  "Follow them, or ask the user before going against one. Cheap: no LLM call.",
                  {"path": _str("File or directory path, absolute or relative to the project root")},
                  required=["path"]),
            _tool("memory_get",
                  "Full record for a memory id: reasoning, alternatives, evidence, supersedes chain.",
                  {"id": _str("Memory row id, a plain number for a project entry, or "
                               "'g<n>' (e.g. 'g12') for a global entry")},
                  required=["id"]),
            _tool("memory_thread",
                  "Chronological versions of a decision topic. Provide 'id' to walk the supersedes chain, or 'topic' for keyword-overlap grouping (which also searches global decisions when global memory is enabled).",
                  {"id": _str("Decision ID to walk the chain, plain number for project, "
                               "'g<n>' for global"),
                   "topic": _str("Keyword for decision group"),
                   "topic_or_id": _str("Deprecated: use id or topic instead"),
                   "limit": _int("Max entries", default=20)},
                  required=[]),
            _tool("decision_audit_score",
                  "Completeness score 0/4..4/4 for a decision: reasoning, alternatives, evidence, supersedes.",
                  {"id": _str("Decision row id, plain number for project, 'g<n>' for global")},
                  required=["id"]),
            _tool("memory_save",
                  "Run the helper-LLM extractor over recent session entries. Hosts should "
                  "call this at session boundaries (handoff, before exit) so structured "
                  "memory survives the next session.",
                  {}),
        ]


# ---- tool handlers --------------------------------------------------

def _h_recall(srv: CairnMCPServer, args: dict) -> str:
    out = srv.project.memory.recall(
        args.get("query", ""), limit=int(args.get("limit", 5)), path=args.get("path") or None
    )
    return out or "(no relevant memory)"


def _h_store(srv: CairnMCPServer, args: dict) -> str:
    srv.project.memory.store(
        args.get("prompt", ""),
        args.get("response", ""),
        importance=int(args.get("importance", 1)),
        scope_path=args.get("scope_path") or None,
        scope=args.get("scope", "project"),
    )
    srv.project.memory.flush()
    srv._on_store()
    return "stored"


def _h_memory_save(srv: CairnMCPServer, args: dict) -> str:
    """Trigger #1: explicit MCP memory_save tool. Hosts call at session boundaries."""
    extraction = srv._maybe_autosave(reason="explicit", force=True)
    if extraction is None:
        return "no extraction (helper unavailable or no recent entries)"
    res = {
        "ok": True,
        "facts": len(extraction.get("facts", [])),
        "decisions": len(extraction.get("decisions", [])),
        "tasks_completed": len(extraction.get("tasks_completed", [])),
        "tasks_in_progress": len(extraction.get("tasks_in_progress", [])),
    }
    if extraction.get("_fallback"):
        res["fallback"] = True
    return json.dumps(res, ensure_ascii=False)


def _h_recent(srv: CairnMCPServer, args: dict) -> str:
    rows = srv.project.memory.recent(n=int(args.get("n", 10)))
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _h_search(srv: CairnMCPServer, args: dict) -> str:
    rows = srv.project.memory.search(
        args.get("query", ""), limit=int(args.get("limit", 10)), path=args.get("path") or None
    )
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _h_invalidate(srv: CairnMCPServer, args: dict) -> str:
    ok = srv.project.memory.invalidate(args["id"], reason=args.get("reason", ""))
    return "invalidated" if ok else "not found or already invalid"


def _h_boot(srv: CairnMCPServer, args: dict) -> str:
    fmt = args.get("format", "text")
    extras = args.get("include_memory") or []
    return srv.project.injector.build(
        fmt=fmt, include_memory=extras or None, decisions=srv.project.memory.active_decisions()
    ) or "(empty)"


def _h_decisions_for_path(srv: CairnMCPServer, args: dict) -> str:
    rows = srv.project.memory.decisions_for_path(args.get("path", ""))
    return format_brief(rows, f"Decisions that apply to {args.get('path', '')}:") or \
        "(no path-scoped decisions for this file)"


def _h_status(srv: CairnMCPServer, _args: dict) -> str:
    state = srv.project.session.load_state()
    handoff = srv.project.session.load_handoff()
    out = [
        f"task: {state.get('current_task') or 'none'}",
        f"status: {state.get('task_status', 'idle')}",
        f"session #: {state.get('session_count', 0)}",
        f"failed writes: {getattr(srv.project.memory, '_failed_writes', 0)}",
    ]
    if handoff:
        out.append("\nlast handoff:\n" + handoff[:500])
    return "\n".join(out)


def _h_check_approach(srv: CairnMCPServer, args: dict) -> str:
    rows = srv.project.memory.check_rejected(args.get("approach", ""), limit=int(args.get("limit", 5)))
    return json.dumps(rows, ensure_ascii=False, indent=2) if rows else "(no rejected or abandoned match found)"


def _h_record_decision(srv: CairnMCPServer, args: dict) -> str:
    supersedes = args.get("supersedes")
    if supersedes is not None:
        supersedes = str(supersedes) if supersedes else None
    warning = None
    if supersedes is not None:
        prior = srv.project.memory.get_full(supersedes)
        if not prior or prior.get("valid_until"):
            warning = f"supersedes id {supersedes} not found or already invalidated"

    # Opt-in (memory.conflict.enabled, default false): check against prior
    # decisions *before* inserting the new one. No-op, zero LLM calls, when
    # disabled, see Memory.check_conflicts / conflict.py.
    conflicts = srv.project.memory.check_conflicts(
        args["decision"], args.get("reasoning") or ""
    )

    new_id = srv.project.memory.record_decision(
        decision=args["decision"],
        reasoning=args.get("reasoning") or None,
        alternatives=args.get("alternatives") or None,
        evidence=args.get("evidence") or None,
        supersedes=supersedes,
        chose_because=args.get("chose_because") or None,
        importance=int(args.get("importance", 2)),
        scope_path=args.get("scope_path") or None,
        scope=args.get("scope", "project"),
    )
    res = {"id": new_id, "kind": "decision"}
    if warning:
        res["warning"] = warning
    if conflicts:
        res["conflicts"] = conflicts
    return json.dumps(res, ensure_ascii=False)


def _h_memory_get(srv: CairnMCPServer, args: dict) -> str:
    full = srv.project.memory.get_full(args["id"])
    return json.dumps(full, ensure_ascii=False, indent=2) if full else "(not found)"


def _h_memory_thread(srv: CairnMCPServer, args: dict) -> str:
    target: int | str
    if args.get("id") is not None:
        raw = args["id"]
        target = raw if isinstance(raw, str) and raw.startswith("g") else int(raw)
    elif args.get("topic") is not None:
        target = str(args["topic"])
    elif args.get("topic_or_id") is not None:
        raw = args["topic_or_id"]
        if isinstance(raw, str) and raw.startswith("g") and raw[1:].isdigit():
            target = raw
        else:
            try:
                target = int(raw)
            except (ValueError, TypeError):
                target = str(raw)
    else:
        return json.dumps({"error": "Missing parameter: 'id' or 'topic' is required"}, ensure_ascii=False)

    rows = srv.project.memory.thread(target, limit=int(args.get("limit", 20)))
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _h_decision_audit_score(srv: CairnMCPServer, args: dict) -> str:
    return json.dumps(
        srv.project.memory.decision_audit_score(args["id"]),
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
    "check_approach": _h_check_approach,
    "decisions_for_path": _h_decisions_for_path,
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
