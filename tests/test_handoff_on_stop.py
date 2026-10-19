"""The Stop hook can keep session-handoff.md current after every turn (opt-in `memory.handoff_on_stop`)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from cairn.memory import handoff
from cairn.memory.memory import Memory

CLI = "import sys; sys.argv=['cairn', 'memory', 'nudge']; from cairn.interface.cli import main; main()"


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        r = Path(d)
        (r / ".cairn").mkdir()
        yield r


def _write_config(root: Path, text: str) -> None:
    (root / ".cairn" / "config.yaml").write_text(text, encoding="utf-8")


def _mem(root: Path) -> Memory:
    return Memory({"memory": {"async_store": False, "global_enabled": False}}, project_dir=root)


def _stop_hook(root: Path, **payload) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", CLI], input=json.dumps({"cwd": str(root), **payload}),
                          capture_output=True, text=True, encoding="utf-8", cwd=root)


def test_refresh_light_needs_no_project_and_keeps_the_agents_note(root):
    assert handoff.refresh_light(root, {}) is None                    # no database yet
    m = _mem(root)
    m.record_decision("Money is integer cents", scope_path="shop")
    m.close()
    path = root / ".cairn" / "persistence" / "session-handoff.md"
    path.write_text("# Session Handoff: x\n\n## Continuation Point\nWrite apply_discount\n", encoding="utf-8")
    assert handoff.refresh_light(root, {}) == path
    text = path.read_text(encoding="utf-8")
    assert "Write apply_discount" in text and "[shop] Money is integer cents" in text
    assert text.count("## Recent Decisions") == 1
    assert handoff.refresh_light(root / "elsewhere", {}) is None


def test_stop_hook_refreshes_the_handoff_only_when_enabled(root):
    m = _mem(root)
    m.record_decision("Use pnpm", scope_path="web")
    m.close()
    path = root / ".cairn" / "persistence" / "session-handoff.md"

    _write_config(root, "memory: {async_store: false, global_enabled: false}\n")
    assert _stop_hook(root).returncode == 0 and not path.exists()           # off by default

    _write_config(root, "memory: {async_store: false, global_enabled: false, handoff_on_stop: true}\n")
    started = time.monotonic()
    out = _stop_hook(root)
    elapsed = time.monotonic() - started
    assert out.returncode == 0 and "[web] Use pnpm" in path.read_text(encoding="utf-8")
    assert elapsed < 5                                                     # a cold interpreter start dominates this

    path.unlink()
    assert _stop_hook(root, stop_hook_active=True).returncode == 0 and not path.exists()


def test_a_broken_database_never_breaks_the_hook(root):
    _write_config(root, "memory: {handoff_on_stop: true}\n")
    db = root / ".cairn" / "persistence" / "memory.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"not a database")
    assert _stop_hook(root).returncode == 0
