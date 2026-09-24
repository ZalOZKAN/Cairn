"""
Cairn, a portable `.cairn/` folder for any AI coding agent.

Decay-aware SQLite memory, decision provenance, project manifests, MCP server.
Works with Claude Code, Cursor, Antigravity, or your own CLI, no
integration code, just a folder of markdown + a SQLite file.
"""
__version__ = "0.5.0-dev0"

from cairn.core import Project, Agent
from cairn.memory.memory import Memory

__all__ = ["Project", "Agent", "Memory"]
