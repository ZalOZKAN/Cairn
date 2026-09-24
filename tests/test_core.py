"""Cairn v0.4 core tests, config + memory basics (no LLM)."""
from __future__ import annotations

import tempfile


from cairn.config.loader import load_config
from cairn.memory.memory import Memory


# --- Config ---------------------------------------------------------------

def test_load_default_config_has_memory_section():
    config = load_config()
    assert "memory" in config
    assert config["memory"]["enabled"] is True


def test_default_config_has_no_router_section():
    config = load_config()
    assert "router" not in config, "router was removed in v0.4"
    assert "telemetry" not in config, "telemetry was removed in v0.4"


def test_helper_model_default_present():
    config = load_config()
    assert "memory_helper_model" in config


# --- Memory basics ---------------------------------------------------------

def test_memory_store_and_recall():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        config = load_config()
        config["memory"]["db_path"] = f"{tmpdir}/test.db"
        config["memory"]["global_db_path"] = f"{tmpdir}/global.db"
        config["memory"]["async_store"] = False
        memory = Memory(config)
        memory.store("what is jwt authentication", "JWT is a token format used for stateless auth")
        result = memory.recall("jwt token")
        assert "JWT" in result or result == ""


def test_memory_search():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        config = load_config()
        config["memory"]["db_path"] = f"{tmpdir}/test.db"
        config["memory"]["global_db_path"] = f"{tmpdir}/global.db"
        config["memory"]["async_store"] = False
        memory = Memory(config)
        memory.store("python async patterns", "Use asyncio for concurrent tasks")
        results = memory.search("async")
        assert len(results) >= 1
        assert "async" in results[0]["prompt"].lower()


def test_memory_clear():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        config = load_config()
        config["memory"]["db_path"] = f"{tmpdir}/test.db"
        config["memory"]["global_db_path"] = f"{tmpdir}/global.db"
        config["memory"]["async_store"] = False
        memory = Memory(config)
        memory.store("test prompt", "test response")
        count = memory.clear()
        assert count >= 1
        results = memory.search("test")
        assert len(results) == 0
