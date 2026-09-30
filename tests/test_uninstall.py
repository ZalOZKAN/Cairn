"""cairn uninstall: removes only what Cairn wired in, leaves everything else byte for byte."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from cairn.interface.commands import mcp as mcp_cmd
from cairn.interface.commands.uninstall import strip_block, uninstall
from cairn.memory import agents_md
from cairn.memory.agents_md import BEGIN, END

CLI = "import sys; sys.argv=['cairn', *{args!r}]; from cairn.interface.cli import main; main()"

# Shapes another tool writes into the same shared files.
OTHERTOOL_HELPER = 'node "${CLAUDE_PROJECT_DIR:-.}/.claude/helpers/othertool-hooks.cjs"'
OTHERTOOL_AGENTS_SECTION = "<!-- othertool:start -->\n## OtherTool\nUse othertool ask before exploring.\n<!-- othertool:end -->\n"
OTHERTOOL_MCP = {"mcpServers": {"othertool": {"command": "npx", "args": ["-y", "@nanonets/othertool", "mcp"]}}}
OTHERTOOL_SETTINGS = {
    "hooks": {
        "PostToolUse": [
            {"matcher": "Write|Edit|MultiEdit",
             "hooks": [{"type": "command", "command": f"{OTHERTOOL_HELPER} post-edit", "timeout": 10000}]},
            {"matcher": "Bash|mcp__othertool__|Read|Grep|Glob",
             "hooks": [{"type": "command", "command": f"{OTHERTOOL_HELPER} tool-savings", "timeout": 8000}]},
        ],
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": f"{OTHERTOOL_HELPER} prompt", "timeout": 15000}]}],
        "SessionStart": [{"hooks": [{"type": "command", "command": f"{OTHERTOOL_HELPER} session-start", "timeout": 8000}]}],
        "Stop": [{"hooks": [{"type": "command", "command": f"{OTHERTOOL_HELPER} stop", "timeout": 8000}]}],
    },
    "statusLine": {"type": "command",
                   "command": 'node "${CLAUDE_PROJECT_DIR:-.}/.claude/helpers/othertool-statusline.cjs"'},
    "permissions": {"allow": ["Bash(othertool:*)", "Bash(npx othertool:*)"]},
}


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        yield Path(d)


def _cli(cwd: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", CLI.format(args=list(args))],
                          capture_output=True, text=True, encoding="utf-8", cwd=cwd,
                          env={**os.environ, **(env or {})}, input="")


def _home_env(home: Path) -> dict:
    return {"USERPROFILE": str(home), "HOME": str(home)}


def _gemini_env(root: Path, home: Path) -> dict:
    """PATH starts with a stand-in `gemini`, so the Gemini CLI branch runs the
    same whether or not this machine has the real one."""
    bin_dir = root / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    if sys.platform == "win32":
        (bin_dir / "gemini.cmd").write_text("@echo off\r\n", encoding="utf-8")
    else:
        exe = bin_dir / "gemini"
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)
    return {**_home_env(home), "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")}


def _write_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _snapshot(project: Path) -> dict[str, bytes]:
    return {p.relative_to(project).as_posix(): p.read_bytes()
            for p in sorted(project.rglob("*")) if p.is_file() and ".cairn" not in p.parts}


# ---- strip_block ------------------------------------------------------

def test_strip_block_restores_the_text_cairn_appended_to():
    assert strip_block(f"Hello\n\n{BEGIN}\nstuff\n{END}\n") == "Hello\n"


def test_strip_block_rejoins_text_on_both_sides():
    assert strip_block(f"A\n\n{BEGIN}\nx\n{END}\n\nB\n") == "A\n\nB\n"


def test_strip_block_reports_an_emptied_file_and_a_missing_block():
    assert strip_block(f"{BEGIN}\nx\n{END}\n") == ""
    assert strip_block("no block here\n") is None


# ---- is_cairn_hook_command ----------------------------------------------

@pytest.mark.parametrize("host", sorted(mcp_cmd._HOOK_SNIPPETS))
@pytest.mark.parametrize("windows", [True, False])
@pytest.mark.parametrize("path", ["cairn", "C:\\Users\\Z\\venv\\Scripts\\cairn.exe", "/opt/my tools/cairn"])
def test_every_hook_command_cairn_writes_is_recognized(host, windows, path):
    exe = mcp_cmd.hook_exe(path, host, windows)
    for args in mcp_cmd._hook_args():
        assert mcp_cmd.is_cairn_hook_command(f"{exe} {args}")


def test_python_module_form_is_recognized():
    assert mcp_cmd.is_cairn_hook_command('"C:\\py\\python.exe" -m cairn.interface.cli memory detect')


@pytest.mark.parametrize("command", [
    f"{OTHERTOOL_HELPER} post-edit",
    "othertool memory detect",
    "cairn memory search",
    "",
])
def test_other_commands_are_not_cairns(command):
    assert not mcp_cmd.is_cairn_hook_command(command)


# ---- uninstall(), no subprocess ---------------------------------------------

def test_hooks_written_by_an_older_cairn_path_are_still_removed(root):
    _write_json(root / ".claude" / "settings.json", {"hooks": {
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "C:/old/venv/Scripts/cairn.exe memory detect"}]}],
    }})
    plan = uninstall(root, apply=True)
    assert plan.removed == [(".claude/settings.json", "hooks (UserPromptSubmit) (nothing else in it: file deleted)")]
    assert not (root / ".claude").exists()


def test_a_cairn_command_inside_someone_elses_group_leaves_the_group(root):
    _write_json(root / ".claude" / "settings.json", {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "cairn memory nudge"},
        {"type": "command", "command": "mytool finish"},
    ]}]}})
    uninstall(root, apply=True)
    data = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert data == {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "mytool finish"}]}]}}


def test_unparseable_settings_mentioning_cairn_are_left_alone_with_a_note(root):
    p = root / ".claude" / "settings.json"
    p.parent.mkdir()
    text = '{\n  // mine\n  "hooks": {"Stop": [{"hooks": [{"command": "cairn memory nudge"}]}]}\n}\n'
    p.write_text(text, encoding="utf-8")
    plan = uninstall(root, apply=True)
    assert plan.removed == []
    assert any("not plain JSON" in n for n in plan.notes)
    assert p.read_text(encoding="utf-8") == text


def test_crlf_agents_md_keeps_its_line_endings(root):
    p = root / "AGENTS.md"
    p.write_bytes(f"# Mine\r\n\r\n{BEGIN}\r\nx\r\n{END}\r\n".encode())
    plan = uninstall(root, apply=True)
    assert p.read_bytes() == b"# Mine\r\n"
    assert "AGENTS.md kept: it has content that is not Cairn's" in plan.notes


def test_gemini_context_link_stays_while_agents_md_has_other_content(root):
    (root / "AGENTS.md").write_text(f"# Team notes\n\n{BEGIN}\nx\n{END}\n", encoding="utf-8")
    _write_json(root / ".gemini" / "settings.json", {"context": {"fileName": ["GEMINI.md", "AGENTS.md"]}})
    uninstall(root, apply=True)
    data = json.loads((root / ".gemini" / "settings.json").read_text(encoding="utf-8"))
    assert data == {"context": {"fileName": ["GEMINI.md", "AGENTS.md"]}}


def test_gemini_context_link_cairn_recorded_goes_even_while_agents_md_stays(root):
    (root / ".cairn" / "persistence").mkdir(parents=True)
    (root / "AGENTS.md").write_text(f"# Team notes\n\n{BEGIN}\nx\n{END}\n", encoding="utf-8")
    (root / "GEMINI.md").write_text("# Gemini notes\n", encoding="utf-8")
    assert agents_md.link_gemini(root) is not None
    assert agents_md.wired(root, agents_md.GEMINI_LINK)
    uninstall(root, apply=True)
    assert not (root / ".gemini").exists()
    assert not agents_md.wired(root, agents_md.GEMINI_LINK)


def test_dry_run_lists_exactly_what_apply_then_removes(root):
    (root / "AGENTS.md").write_text(f"{BEGIN}\nx\n{END}\n", encoding="utf-8")
    _write_json(root / ".mcp.json", {"mcpServers": {"cairn": {"command": "cairn", "args": ["mcp", "serve"]}}})
    before = _snapshot(root)
    dry = uninstall(root, apply=False)
    assert _snapshot(root) == before
    assert uninstall(root, apply=True).removed == dry.removed
    assert uninstall(root, apply=True).removed == []


# ---- end to end, through the real CLI ------------------------------------------

def test_init_then_uninstall_leaves_only_cairn_dir(root):
    home = root / "home"
    (home / ".claude").mkdir(parents=True)
    project = root / "project"
    project.mkdir()
    assert _cli(project, "init", "--yes", env=_home_env(home)).returncode == 0
    assert {".mcp.json", ".claude/settings.json", "AGENTS.md"} <= set(_snapshot(project))

    wired = _snapshot(project)
    dry = _cli(project, "uninstall", env=_home_env(home))
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "Would remove:" in dry.stdout and "uninstall --yes" in dry.stdout
    assert _snapshot(project) == wired

    out = _cli(project, "uninstall", "--yes", env=_home_env(home))
    assert out.returncode == 0, out.stdout + out.stderr
    assert "Removed:" in out.stdout
    assert sorted(p.name for p in project.iterdir()) == [".cairn"]
    assert "Nothing of Cairn's" in _cli(project, "uninstall", "--yes", env=_home_env(home)).stdout


def test_init_then_uninstall_restores_othertool_and_user_files_byte_for_byte(root):
    home = root / "home"
    (home / ".claude").mkdir(parents=True)
    project = root / "project"
    project.mkdir()
    (project / "AGENTS.md").write_text("# Project notes\n\nRun make test.\n\n" + OTHERTOOL_AGENTS_SECTION, encoding="utf-8")
    (project / "CLAUDE.md").write_text("# Claude notes\n\nBe brief.\n", encoding="utf-8")
    _write_json(project / ".mcp.json", OTHERTOOL_MCP)
    _write_json(project / ".claude" / "settings.json", OTHERTOOL_SETTINGS)
    original = _snapshot(project)

    assert _cli(project, "init", "--yes", env=_home_env(home)).returncode == 0
    wired = _snapshot(project)
    for name in ("AGENTS.md", "CLAUDE.md", ".mcp.json", ".claude/settings.json"):
        assert wired[name] != original[name], f"init did not touch {name}"
    settings = json.loads(wired[".claude/settings.json"])
    assert settings["statusLine"] == OTHERTOOL_SETTINGS["statusLine"]
    assert settings["hooks"]["UserPromptSubmit"][0] == OTHERTOOL_SETTINGS["hooks"]["UserPromptSubmit"][0]

    out = _cli(project, "uninstall", "--yes", env=_home_env(home))
    assert out.returncode == 0, out.stdout + out.stderr
    assert _snapshot(project) == original


def test_gemini_init_then_uninstall_restores_the_users_settings(root):
    home = root / "home"
    home.mkdir()
    project = root / "project"
    project.mkdir()
    (project / "GEMINI.md").write_text("# Gemini notes\n", encoding="utf-8")
    _write_json(project / ".gemini" / "settings.json",
                {"theme": "Dracula", "mcpServers": {"other": {"command": "other-server"}}})
    original = _snapshot(project)

    env = _gemini_env(root, home)
    assert _cli(project, "init", "--yes", env=env).returncode == 0
    data = json.loads((project / ".gemini" / "settings.json").read_text(encoding="utf-8"))
    assert "cairn" in data["mcpServers"] and "BeforeAgent" in data["hooks"]
    assert data["context"]["fileName"] == ["GEMINI.md", "AGENTS.md"]

    out = _cli(project, "uninstall", "--yes", env=env)
    assert out.returncode == 0, out.stdout + out.stderr
    assert _snapshot(project) == original
