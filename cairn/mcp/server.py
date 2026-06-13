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
from pathlib import Path
from typing import Any

from cairn import __version__


class CairnMCPServer:
    """Minimal MCP server, JSON-RPC 2.0 over stdio."""

    def __init__(self, project_dir: Path | None = None):
        self.project_dir = project_dir or Path.cwd()
        self._project = None  # lazy
        self._tools = self._define_tools()

    # ---- project (lazy) -------------------------------------------------

    @property
    def project(self):
        if self._project is None:
            from cairn.core import Project
            self._project = Project(project_dir=self.project_dir, load_session=False)
        return self._project

    # ---- main loop ------------------------------------------------------

    def run(self) -> None:
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
    return "stored"


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
