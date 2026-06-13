"""
MCP Adapter, connects to Model Context Protocol servers.

Supported MCP servers are defined in config:
  mcp:
    servers:
      - name: filesystem
        url: https://mcp.filesystem.com/sse
      - name: github
        url: https://mcp.github.com/sse

Usage:
    mcp = MCPAdapter(config)
    tools = mcp.list_tools()
    result = mcp.call_tool("filesystem", "read_file", {"path": "src/auth.py"})
"""
from __future__ import annotations

import json
from typing import Any


class MCPAdapter:
    """
    MCP server adapter.
    Config-driven server list plus a simple tool-call surface.
    """

    def __init__(self, config: dict):
        self.config = config
        mcp_cfg = config.get("mcp", {})
        self.servers: list[dict] = mcp_cfg.get("servers", [])
        self._connected: dict[str, Any] = {}

    def list_servers(self) -> list[dict]:
        """List configured MCP servers."""
        return [
            {
                "name": s.get("name"),
                "url": s.get("url"),
                "connected": s.get("name") in self._connected,
            }
            for s in self.servers
        ]

    def list_tools(self, server_name: str | None = None) -> list[dict]:
        """
        List available tools.
        Static definitions for now, a future revision will fetch them
        dynamically from each server.
        """
        builtin_tools = [
            {"name": "read_file", "server": "filesystem", "description": "Read a file"},
            {"name": "write_file", "server": "filesystem", "description": "Write a file"},
            {"name": "list_dir", "server": "filesystem", "description": "List directory"},
            {"name": "web_search", "server": "brave", "description": "Search the web"},
            {"name": "get_issue", "server": "github", "description": "Get GitHub issue"},
        ]
        if server_name:
            return [t for t in builtin_tools if t["server"] == server_name]
        return builtin_tools

    def call_tool(self, server: str, tool: str, params: dict) -> str:
        """
        Call an MCP tool.
        Filesystem is implemented locally; other servers are stubs for now.
        """
        if server == "filesystem":
            return self._filesystem_tool(tool, params)
        return f"[MCP] {server}.{tool} called with {json.dumps(params)}, full MCP protocol not yet implemented"

    def _filesystem_tool(self, tool: str, params: dict) -> str:
        """Local implementation of filesystem MCP tools."""
        import os
        from pathlib import Path

        if tool == "read_file":
            path = Path(params.get("path", ""))
            if path.exists():
                content = path.read_text(encoding="utf-8", errors="replace")
                return content[:5000]  # 5K char limit
            return f"File not found: {path}"

        if tool == "list_dir":
            path = Path(params.get("path", "."))
            if path.is_dir():
                items = list(path.iterdir())
                return "\n".join(str(i.relative_to(path)) for i in sorted(items)[:50])
            return f"Not a directory: {path}"

        if tool == "write_file":
            path = Path(params.get("path", ""))
            content = params.get("content", "")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return f"Written: {path}"

        return f"Unknown filesystem tool: {tool}"

    def inject_into_prompt(self, prompt: str, agent_context: dict | None = None) -> str:
        """
        Append MCP tool info to a prompt so the agent knows which tools
        are available.
        """
        tools = self.list_tools()
        if not tools:
            return prompt
        tools_desc = "\n".join(f"- {t['name']} ({t['server']}): {t['description']}" for t in tools[:10])
        return f"{prompt}\n\n[Available MCP tools:\n{tools_desc}]"
