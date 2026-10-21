"""
Cairn MCP server: exposes a project's `.cairn/` folder as
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
    memory_recall:       semantic + keyword recall, decay-aware
    memory_store:        write a new memory entry
    memory_recent:       last N entries
    memory_search:       substring search
    memory_invalidate:   retire a memory by id with optional reason
    memory_boot:         session boot context
    memory_status:       current task + last handoff
    record_decision:     first-class decision with reasoning/alternatives/evidence/supersedes
    memory_get:          full record including evidence + supersedes chain
    memory_thread:       chronological versions of a decision topic
    decision_audit_score: completeness score 0/4..4/4 for a decision
    check_approach:      has this approach already been rejected or abandoned?
    decisions_for_path:  active decisions scoped to a file's directory
    memory_save:         handoff note for the next session, in any tool
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


# What `tools/list` advertises when `mcp.tools: core` is set. Every tool stays callable by name either way; the
# profile only decides what is sent to the host with each request (about 2,000 tokens for all 14, about 1,000 for these).
CORE_TOOLS = ("record_decision", "check_approach", "decisions_for_path", "memory_save", "memory_boot", "memory_invalidate")


def tool_profile(project_dir: Path) -> str:
    """`core` or `all` from the project's `mcp.tools` setting; anything else, or no setting, means `all`."""
    from cairn.config.loader import load_config
    try:
        value = ((load_config(project_dir / ".cairn" / "config.yaml").get("mcp") or {}).get("tools") or "all")
    except Exception:
        return "all"
    return value if value in ("core", "all") else "all"


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
        # the MCP client's name (e.g. "claude-code", "cursor"), from initialize
        self.client_name: str | None = None
        self._note_saved = False

    # ---- project (lazy) -------------------------------------------------

    @property
    def project(self):
        if self._project is None:
            from cairn.core import Project
            self._project = Project(project_dir=self.project_dir, load_session=False)
        return self._project

    # ---- main loop ------------------------------------------------------

    def run(self) -> None:
        # MCP is UTF-8 JSON-RPC regardless of the host process's own console
        # encoding (cp1252 on many Windows setups unless PYTHONIOENCODING is
        # set); without this, non-ASCII decision text can crash `_send`, and
        # bytes as ordinary as a UTF-8 BOM decode into un-strippable mojibake
        # instead of "\ufeff", silently dropping the first request.
        for stream in (sys.stdin, sys.stdout):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError, OSError):
                pass  # already UTF-8, or a stream (e.g. under a test) that cannot be reconfigured
        try:
            for line in sys.stdin:
                # some shells prepend a UTF-8 BOM to piped input; it would make
                # the first request (initialize) unparseable and silently skipped
                line = line.strip().lstrip("\ufeff")
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
        """Trigger #5: final flush + extract on EOF/SIGTERM. The host closing
        the server is the one session-end signal every MCP host gives, so:
        any AGENTS.md hand edit still pending import (the agent edited the
        block but never called a tool that would have synced it) is imported
        now, rather than sitting stale until something else happens to
        trigger a sync; and the handoff's git and decision sections are
        refreshed for whichever tool opens the project next. A note the agent
        left this session is kept, not overwritten by the automatic save."""
        if self._idle_timer is not None:
            self._idle_timer.cancel()
        if self._project is None:
            return
        try:
            self._project.memory.flush()
            self._project.memory._refresh_agents_md()
            if not self._note_saved:
                self._maybe_autosave(force=True, reason="shutdown")
            from cairn.memory import handoff
            handoff.refresh(self._project.memory, self._project.session.handoff_path)
            self._project.memory.close()
        except Exception as exc:
            print(f"cairn: shutdown save failed: {exc}", file=sys.stderr)

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
            self.client_name = (params.get("clientInfo") or {}).get("name") or None
            # Force Project() now rather than waiting for the first tool call:
            # its constructor imports any AGENTS.md hand edit still pending
            # from a previous session (Project.sync_decision_files ->
            # Memory._refresh_agents_md). A session that never happens to
            # call a Cairn tool would otherwise leave that edit un-imported
            # indefinitely, since _shutdown() also only runs the import if
            # `.project` was ever touched, and cannot assume the host closes
            # this process's stdin cleanly rather than terminating it outright.
            try:
                self.project  # noqa: B018
            except Exception as exc:
                print(f"cairn: startup sync failed: {exc}", file=sys.stderr)
            return self._respond(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "cairn", "version": __version__},
                "instructions": AGENT_GUIDE,
            })
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            listed = [t for t in self._tools if t["name"] in CORE_TOOLS] if tool_profile(self.project_dir) == "core" else self._tools
            return self._respond(req_id, {"tools": listed})
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
        # Descriptions are sent with every request of every host, so they are kept short: what the tool is for and
        # when to call it. What the response contains is in the response itself. Names, parameters and defaults are
        # a published surface and do not change.
        return [
            _tool("memory_recall",
                  "Decay-aware recall of past memory relevant to a query.",
                  {"query": _str("Search query"),
                   "limit": _int("Max entries", default=5),
                   "path": _str("Workspace-relative path: also matches its ancestors. Omit for repo-wide.")},
                  required=["query"]),
            _tool("memory_store",
                  "Store a memory entry. Use scope='global' unprompted for a user preference that would hold in any "
                  "project ('I always use TypeScript'); facts about this codebase stay 'project'.",
                  {"prompt": _str("Topic"),
                   "response": _str("Fact to remember"),
                   "importance": _int("1 normal, 2 decision, 3 critical", default=1),
                   "scope_path": _str("Workspace-relative path it applies to. Omit for repo-wide."),
                   "scope": _str("'project' or 'global' (~/.cairn/global.db)", default="project")},
                  required=["prompt", "response"]),
            _tool("memory_recent",
                  "Most recent valid memory entries.",
                  {"n": _int("How many", default=10)}),
            _tool("memory_search",
                  "Substring search across memory.",
                  {"query": _str("Substring"),
                   "limit": _int("Max results", default=10),
                   "path": _str("Workspace-relative path to scope to")},
                  required=["query"]),
            _tool("memory_invalidate",
                  "Retire a memory entry by id (kept, not deleted).",
                  {"id": _str("Number, or 'g<n>' for a global entry"),
                   "reason": _str("Why", default="")},
                  required=["id"]),
            _tool("memory_boot",
                  "Session boot context.",
                  {"format": {"type": "string", "enum": ["text", "claude", "json"]},
                   "decision_mode": {"type": "string", "enum": ["full", "index"], "default": "index",
                                    "description": "index: one line per decision (fetch with memory_get); full: every entry"},
                   "token_budget": _int("Approximate token cap for the whole boot text; decisions that do not fit stay as index lines"),
                   "include_memory": {"type": "array", "items": {"type": "string"},
                                      "description": "Extra memory file names"}}),
            _tool("memory_status",
                  "Current task, status and last handoff.",
                  {}),
            _tool("record_decision",
                  "Record a settled decision with reasoning and rejected alternatives. Use supersedes or replaces "
                  "to link an earlier decision; conflicts and candidates are warnings.",
                  {"decision": _str("The decision in one sentence"),
                   "reasoning": _str("Why", default=""),
                   "alternatives": {"type": "array", "description": "Rejected options",
                                    "items": {"type": "object", "properties": {
                                        "option": {"type": "string"},
                                        "rejected_because": {"type": "string"}}}},
                   "evidence": {"type": "array", "description": "Supporting sources",
                                "items": {"type": "object", "properties": {
                                    "source": {"type": "string"},
                                    "claim": {"type": "string"}}}},
                   "supersedes": _str("Id of the decision this replaces (number, or 'g<n>')"),
                   "replaces": _str("A phrase from the decision this replaces, when the id is unknown"),
                   "valid_from": _str("ISO-8601 time the decision took effect; past only, default now"),
                   "chose_because": _str("Short tag for the deciding factor", default=""),
                   "importance": _int("1 normal, 2 decision, 3 critical", default=2),
                   "scope_path": _str("Workspace-relative path it applies to. Omit for repo-wide."),
                   "scope": _str("'project' or 'global' (~/.cairn/global.db)", default="project"),
                   "forbid": {"type": "array", "items": {"type": "string"},
                              "description": "Hard rules only ('never', 'no X anywhere'): regexes that added code "
                                             "must not match (the edit hook blocks it). Project scope only."}},
                  required=["decision"]),
            _tool("check_approach",
                  "Call BEFORE proposing an approach: was something like it already rejected or abandoned, and why? "
                  "Empty means no record, not that it is safe.",
                  {"approach": _str("The approach, in a sentence or two"),
                   "limit": _int("Max matches", default=5)},
                  required=["approach"]),
            _tool("decisions_for_path",
                  "Call BEFORE editing or creating a file: the active decisions for its directory (NEEDS REVIEW if "
                  "the code changed a lot since). Follow them or ask the user. No LLM call.",
                  {"path": _str("File or directory, absolute or relative to the project root")},
                  required=["path"]),
            _tool("memory_get",
                  "Full record: reasoning, alternatives, evidence, supersedes chain.",
                  {"id": _str("Number, or 'g<n>' for a global entry")},
                  required=["id"]),
            _tool("memory_thread",
                  "A decision topic over time: pass id (supersedes chain) or topic.",
                  {"id": _str("Decision id (number, or 'g<n>')"),
                   "topic": _str("Keyword"),
                   "topic_or_id": _str("Deprecated: use id or topic"),
                   "as_of": _str("ISO-8601 time; returns only versions active then"),
                   "limit": _int("Max entries", default=20)},
                  required=[]),
            _tool("decision_audit_score",
                  "Completeness 0/4 to 4/4: reasoning, alternatives, evidence, supersedes.",
                  {"id": _str("Decision id (number, or 'g<n>')")},
                  required=["id"]),
            _tool("memory_save",
                  "Leave a handoff note when you stop with work unfinished, for the next session in any tool. Pass "
                  "what was done and the next step the user asked for (empty when complete, never invented). Do it "
                  "last; it does not start the next step. With no arguments it runs the optional helper-model "
                  "extractor.",
                  {"summary": _str("What was done, a few sentences"),
                   "next_step": _str("The next open step from the user's request; empty when done")}),
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
    summary = (args.get("summary") or "").strip()
    next_step = (args.get("next_step") or "").strip()
    if summary or next_step:
        from cairn.memory import handoff
        path = handoff.save_note(srv.project, summary, next_step, source=srv.client_name)
        srv._note_saved = True
        return json.dumps({"ok": True, "handoff": str(path)}, ensure_ascii=False)
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
    mode = args.get("decision_mode", "index")
    budget = args.get("token_budget")
    budget = int(budget) if budget is not None else None
    index = mode == "index"
    if budget is None:
        decisions = srv.project.memory.decisions_block(detail=not index)
    else:
        # The budget covers the whole boot text: measure the non-decision
        # parts first and give the decisions what is left.
        base = srv.project.injector.estimate_tokens(fmt=fmt, include_memory=extras or None)
        decisions = srv.project.memory.decisions_block(
            detail=not index, max_tokens=max(budget - base, 0)
        )
    return srv.project.injector.build(
        fmt=fmt, include_memory=extras or None, decisions=decisions
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
    replace_candidates = None
    if supersedes is None and args.get("replaces"):
        found = srv.project.memory.resolve_replaces(args["replaces"])
        if found["id"] is not None:
            supersedes = str(found["id"])
        elif found["candidates"]:
            replace_candidates = found["candidates"]
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
    duplicates = srv.project.memory.duplicate_candidates(
        args["decision"], args.get("reasoning"), args.get("scope_path")
    )
    conflict_candidates = srv.project.memory.conflict_candidates(
        args["decision"], args.get("reasoning"), args.get("scope_path")
    )

    try:
        new_id = _record(srv, args, supersedes)
    except ValueError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    res = {"id": new_id, "kind": "decision"}
    if warning:
        res["warning"] = warning
    if conflicts:
        res["conflicts"] = conflicts
    if duplicates:
        res["duplicate_candidates"] = duplicates
    if conflict_candidates:
        res["conflict_candidates"] = conflict_candidates
    if replace_candidates:
        res["replace_candidates"] = replace_candidates
    elif supersedes is None and args.get("scope", "project") != "global":
        possible = srv.project.memory.possible_supersedes(
            args["decision"], args.get("scope_path"), exclude_id=new_id)
        if possible:
            res["possible_supersedes"] = possible
    return json.dumps(res, ensure_ascii=False)


def _record(srv: CairnMCPServer, args: dict, supersedes: str | None) -> int | str:
    return srv.project.memory.record_decision(
        decision=args["decision"],
        reasoning=args.get("reasoning") or None,
        alternatives=args.get("alternatives") or None,
        evidence=args.get("evidence") or None,
        supersedes=supersedes,
        chose_because=args.get("chose_because") or None,
        importance=int(args.get("importance", 2)),
        scope_path=args.get("scope_path") or None,
        scope=args.get("scope", "project"),
        recorded_by=srv.client_name,
        forbid=args.get("forbid") or None,
        valid_from=args.get("valid_from") or None,
    )


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

    rows = srv.project.memory.thread(
        target, limit=int(args.get("limit", 20)), as_of=args.get("as_of") or None
    )
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
