"""Config loader v0.4: memory-focused, no router/cost section."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "memory": {
        "enabled": True,
        "backend": "sqlite",
        "max_results": 5,
        # None = <project>/.cairn/persistence/memory.db when the project has a .cairn/
        # folder, else ~/.cairn/memory.db. See memory.resolve_db_path.
        "db_path": None,
        "global_enabled": True,
        "global_db_path": "~/.cairn/global.db",
        "registry_path": "~/.cairn/projects.json",
        "redact": {"enabled": True, "extra_patterns": []},
        "async_store": True,
        "sliding_window": 10,
        "recency_boost": 1.5,
        "decay": {"enabled": True, "half_life_days": 30},
        "dedup": {"enabled": True, "threshold": 0.92},
        # A path-scoped decision is flagged "needs review" once this many
        # commits have touched its path since it was recorded or confirmed.
        "stale": {"enabled": True, "commits": 10},
        # Keep every active decision in a managed block of the project's AGENTS.md.
        "agents_md": {"enabled": True},
        # Stop-hook reminder threshold: file edits since the last record_decision.
        "nudge": {"edits": 8},
        # Opt-in: the Stop hook also refreshes .cairn/persistence/session-handoff.md after every turn.
        "handoff_on_stop": False,
        "conflict": {
            "enabled": False,
            "model": "groq/llama-3.3-70b-versatile",
        },
        "auto_save": {
            "enabled": True,
            "every_n_stores": 30,
            "cooldown_minutes": 10,
            "idle_minutes": 10,
            "skip_if_helper_unavailable": True,
        },
    },
    "context": {
        "enabled": True,
        "max_tokens": 800,
        "summarize": {
            "enabled": False,
            "threshold_tokens": 1200,
            "target_tokens": 400,
            "model": "groq/llama-3.3-70b-versatile",
        },
    },
    # Single optional helper model used for memory housekeeping
    # (extractor, manifest generation, conflict detection, summarization).
    # Off unless a config sets it; `cairn doctor` lists what is skipped.
    "memory_helper_model": None,
    "mcp": {"servers": []},
}


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_CONFIG)
    search_paths = [
        Path.home() / ".cairn" / "config.yaml",
        Path.cwd() / ".cairn" / "config.yaml",
    ]
    if config_path:
        search_paths.append(Path(config_path))
    for path in search_paths:
        if path.exists():
            with open(path, encoding="utf-8") as f:
                user_config = yaml.safe_load(f) or {}
            config = _deep_merge(config, user_config)
    config["_api_keys"] = {
        "anthropic": os.environ.get("ANTHROPIC_API_KEY"),
        "openai": os.environ.get("OPENAI_API_KEY"),
        "google": os.environ.get("GOOGLE_API_KEY"),
        "groq": os.environ.get("GROQ_API_KEY"),
    }
    return config


def _deep_merge(base: dict, override: dict) -> dict:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
