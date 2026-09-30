"""cairn init --dry-run: writes nothing, and lists exactly the files the real run then changes."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

CLI = "import sys; sys.argv=['cairn', *{args!r}]; from cairn.interface.cli import main; main()"
ROW = re.compile(r"^  ([^\s:][^:]*): ")


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        yield Path(d)


def _env(root: Path, home: Path) -> dict:
    """Home with a Claude Code marker, a stand-in `gemini` first on PATH, and a
    wide console so Rich never wraps a listed line."""
    bin_dir = root / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    if sys.platform == "win32":
        (bin_dir / "gemini.cmd").write_text("@echo off\r\n", encoding="utf-8")
    else:
        exe = bin_dir / "gemini"
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    return {"USERPROFILE": str(home), "HOME": str(home), "COLUMNS": "250",
            "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")}


def _cli(cwd: Path, *args: str, env: dict, input: str = "") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", CLI.format(args=list(args))],
                          capture_output=True, text=True, encoding="utf-8", cwd=cwd,
                          env={**os.environ, **env}, input=input)


def _snapshot(d: Path) -> dict[str, bytes]:
    return {p.relative_to(d).as_posix(): p.read_bytes() for p in sorted(d.rglob("*")) if p.is_file()}


def _listed(stdout: str) -> dict[str, str]:
    return {m.group(1): line for line in stdout.splitlines()
            if (m := ROW.match(line)) and m.group(1) != "note"}


def _changed(before: dict[str, bytes], after: dict[str, bytes]) -> set[str]:
    return {k for k, v in after.items() if before.get(k) != v and not k.startswith(".cairn/")}


def _bare(project: Path) -> None:
    pass


def _with_user_files(project: Path) -> None:
    (project / "CLAUDE.md").write_text("# Claude notes\n", encoding="utf-8")
    (project / "GEMINI.md").write_text("# Gemini notes\n", encoding="utf-8")
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}, indent=2) + "\n",
                                       encoding="utf-8")


def _unparseable_mcp(project: Path) -> None:
    (project / ".mcp.json").write_text('{\n  // mine\n  "mcpServers": {}\n}\n', encoding="utf-8")


@pytest.mark.parametrize("setup", [_bare, _with_user_files, _unparseable_mcp])
def test_dry_run_writes_nothing_and_lists_what_init_then_changes(root, setup):
    home, project = root / "home", root / "project"
    project.mkdir()
    env = _env(root, home)
    setup(project)
    before, home_before = _snapshot(project), _snapshot(home)

    dry = _cli(project, "init", "--dry-run", "--yes", env=env)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "Dry run: nothing was written." in dry.stdout
    assert _snapshot(project) == before and _snapshot(home) == home_before
    listed = _listed(dry.stdout)

    real = _cli(project, "init", "--yes", env=env)
    assert real.returncode == 0, real.stdout + real.stderr
    changed = _changed(before, _snapshot(project))
    assert {k for k in listed if not k.startswith(("~", ".cairn/"))} == changed
    assert ".cairn/" in listed and (project / ".cairn").is_dir()
    assert "~/.cairn/projects.json" in listed and (home / ".cairn" / "projects.json").is_file()


def test_dry_run_lists_gemini_context_link_once_when_both_steps_would_add_it(root):
    home, project = root / "home", root / "project"
    project.mkdir()
    (project / "GEMINI.md").write_text("# Gemini notes\n", encoding="utf-8")
    dry = _cli(project, "init", "--dry-run", "--yes", env=_env(root, home))
    line = _listed(dry.stdout)[".gemini/settings.json"]
    assert line.count("context.fileName AGENTS.md") == 1
    assert "mcpServers.cairn" in line and "hooks (BeforeAgent, BeforeTool)" in line


def test_dry_run_in_an_existing_project_lists_only_host_registration(root):
    home, project = root / "home", root / "project"
    project.mkdir()
    env = _env(root, home)
    assert _cli(project, "init", env=env, input="n\n").returncode == 0
    dry = _cli(project, "init", "--dry-run", env=env)
    assert dry.returncode == 0, dry.stdout + dry.stderr
    listed = _listed(dry.stdout)
    assert set(listed) == {".mcp.json", ".claude/settings.json", ".gemini/settings.json"}
    assert ".cairn/ already exists" in dry.stdout
    assert "asks before registering" in dry.stdout
