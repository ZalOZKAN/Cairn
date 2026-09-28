"""One-command host setup: detection + project-scoped MCP/hook registration."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from cairn.interface.commands import mcp as mcp_cmd

CLI = "import sys; sys.argv=['cairn', *{args!r}]; from cairn.interface.cli import main; main()"
NO_GEMINI = lambda name: None  # noqa: E731 - isolates directory-marker tests from this machine's real PATH


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        yield Path(d)


def _cli(root: Path, *args: str, env: dict | None = None, input: str = "") -> subprocess.CompletedProcess:
    """Runs the real CLI in a subprocess. stdin is always a pipe (empty unless
    `input` says otherwise), so no prompt can ever wait on the test runner's
    own terminal."""
    import os
    full_env = {**os.environ, **(env or {})}
    return subprocess.run([sys.executable, "-c", CLI.format(args=list(args))],
                          capture_output=True, text=True, encoding="utf-8", cwd=root,
                          env=full_env, input=input)


def _env_with_fake_gemini(root: Path, home: Path) -> dict:
    """A subprocess env whose PATH starts with a stand-in `gemini` executable
    and whose home is `home`, so the Gemini CLI branch runs the same on a
    machine that has the real one and on one that does not."""
    import os
    bin_dir = root / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    if sys.platform == "win32":
        (bin_dir / "gemini.cmd").write_text("@echo off\r\n", encoding="utf-8")
    else:
        exe = bin_dir / "gemini"
        exe.write_text("#!/bin/sh\n", encoding="utf-8")
        exe.chmod(0o755)
    return {"USERPROFILE": str(home), "HOME": str(home),
            "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", "")}


# ---- detect_hosts -----------------------------------------------------

def test_detect_hosts_finds_only_markers_that_exist(root):
    (root / ".claude").mkdir()
    (root / ".cursor").mkdir()
    assert set(mcp_cmd.detect_hosts(home=root, which=NO_GEMINI)) == {"claude-code", "cursor"}


def test_detect_hosts_empty_home_finds_nothing(root):
    assert mcp_cmd.detect_hosts(home=root, which=NO_GEMINI) == []


def test_detect_hosts_finds_gemini_cli_by_path_not_home_marker(root):
    """~/.gemini alone must not count (Antigravity also uses it); only a real
    `gemini` executable on PATH should, since detect_hosts() cannot see the
    home fixture's marker dirs for this one, only `which`."""
    assert mcp_cmd.detect_hosts(home=root, which=NO_GEMINI) == []
    assert mcp_cmd.detect_hosts(home=root, which=lambda name: "/usr/bin/gemini") == ["gemini-cli"]


# ---- install_mcp_json --------------------------------------------------

def test_install_mcp_json_creates_fresh_file(root):
    action = mcp_cmd.install_mcp_json(root, "cairn")
    assert action == "added"
    data = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
    assert data == {"mcpServers": {"cairn": {"command": "cairn", "args": ["mcp", "serve"]}}}


def test_install_mcp_json_preserves_other_servers(root):
    (root / ".mcp.json").write_text(json.dumps({"mcpServers": {"other-tool": {"command": "other"}}}), encoding="utf-8")
    mcp_cmd.install_mcp_json(root, "cairn")
    data = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["other-tool"] == {"command": "other"}
    assert data["mcpServers"]["cairn"] == {"command": "cairn", "args": ["mcp", "serve"]}


def test_install_mcp_json_is_idempotent(root):
    mcp_cmd.install_mcp_json(root, "cairn")
    assert mcp_cmd.install_mcp_json(root, "cairn") == "already"


def test_install_mcp_json_reports_updated_when_pointing_elsewhere(root):
    mcp_cmd.install_mcp_json(root, "/old/path/cairn")
    assert mcp_cmd.install_mcp_json(root, "/new/path/cairn") == "updated"


def test_install_mcp_json_refuses_to_clobber_unparseable_file(root):
    (root / ".mcp.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        mcp_cmd.install_mcp_json(root, "cairn")
    assert (root / ".mcp.json").read_text(encoding="utf-8") == "{not json"


# ---- install_gemini_mcp_json --------------------------------------------

def test_install_gemini_mcp_json_matches_the_shape_gemini_mcp_add_writes(root):
    """Shape verified live against `gemini mcp add cairn ... --scope project`
    (gemini-cli 0.61.0, 2026-09-27): {"mcpServers": {name: {command, args}}}
    in .gemini/settings.json, same as .mcp.json."""
    action = mcp_cmd.install_gemini_mcp_json(root, "cairn")
    assert action == "added"
    data = json.loads((root / ".gemini" / "settings.json").read_text(encoding="utf-8"))
    assert data == {"mcpServers": {"cairn": {"command": "cairn", "args": ["mcp", "serve"]}}}


def test_install_gemini_mcp_json_preserves_other_settings(root):
    settings = root / ".gemini" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"context": {"fileName": ["GEMINI.md", "AGENTS.md"]}}), encoding="utf-8")
    mcp_cmd.install_gemini_mcp_json(root, "cairn")
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["context"]["fileName"] == ["GEMINI.md", "AGENTS.md"]
    assert data["mcpServers"]["cairn"] == {"command": "cairn", "args": ["mcp", "serve"]}


def test_install_gemini_mcp_json_is_idempotent(root):
    mcp_cmd.install_gemini_mcp_json(root, "cairn")
    assert mcp_cmd.install_gemini_mcp_json(root, "cairn") == "already"


# ---- install_claude_hooks / claude_hook_status ------------------------

def test_install_claude_hooks_creates_fresh_file_with_matcher_intact(root):
    action = mcp_cmd.install_claude_hooks(root, "cairn")
    assert action == "added"
    data = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    pre = data["hooks"]["PreToolUse"][0]
    assert pre["matcher"] == "Edit|Write|MultiEdit"
    assert pre["hooks"][0]["command"] == "cairn memory for-path --hook"
    assert set(data["hooks"]) == {"SessionStart", "PreToolUse", "UserPromptSubmit", "Stop"}


def test_install_claude_hooks_preserves_an_unrelated_hook_on_the_same_event(root):
    settings = root / ".claude"
    settings.mkdir()
    (settings / "settings.json").write_text(json.dumps({
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "my-linter"}]}]}
    }), encoding="utf-8")
    mcp_cmd.install_claude_hooks(root, "cairn")
    data = json.loads((settings / "settings.json").read_text(encoding="utf-8"))
    commands = {h["command"] for g in data["hooks"]["PreToolUse"] for h in g["hooks"]}
    assert commands == {"my-linter", "cairn memory for-path --hook"}


def test_install_claude_hooks_is_idempotent(root):
    mcp_cmd.install_claude_hooks(root, "cairn")
    assert mcp_cmd.install_claude_hooks(root, "cairn") == "already"
    data = json.loads((root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert len(data["hooks"]["SessionStart"]) == 1  # not duplicated


def test_claude_hook_status_reports_per_event_presence(root):
    assert mcp_cmd.claude_hook_status(root, "cairn") == {
        "SessionStart": False, "PreToolUse": False, "UserPromptSubmit": False, "Stop": False}
    mcp_cmd.install_claude_hooks(root, "cairn")
    assert all(mcp_cmd.claude_hook_status(root, "cairn").values())


# ---- install_gemini_hooks / gemini_hook_status -------------------------

def test_install_gemini_hooks_writes_the_shape_gemini_cli_documents(root):
    """Shape from Gemini CLI 0.61.0's own hooks reference, and the same event
    name/matcher the live BeforeAgent probe (2026-09-28) fired under."""
    assert mcp_cmd.install_gemini_hooks(root, "cairn") == "added"
    data = json.loads((root / ".gemini" / "settings.json").read_text(encoding="utf-8"))
    assert data == {"hooks": {"BeforeAgent": [{"matcher": "*", "hooks": [
        {"name": "cairn-detect", "type": "command", "command": "cairn memory detect"}]}]}}


def test_install_gemini_hooks_keeps_unrelated_hooks_and_the_mcp_entry(root):
    settings = root / ".gemini" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({
        "mcpServers": {"cairn": {"command": "cairn", "args": ["mcp", "serve"]}},
        "hooks": {"BeforeAgent": [{"matcher": "*", "hooks": [{"type": "command", "command": "my-guard"}]}]},
    }), encoding="utf-8")
    assert mcp_cmd.install_gemini_hooks(root, "cairn") == "added"
    data = json.loads(settings.read_text(encoding="utf-8"))
    commands = [h["command"] for g in data["hooks"]["BeforeAgent"] for h in g["hooks"]]
    assert commands == ["my-guard", "cairn memory detect"]
    assert data["mcpServers"]["cairn"]["command"] == "cairn"


def test_install_gemini_hooks_is_idempotent(root):
    mcp_cmd.install_gemini_hooks(root, "cairn")
    assert mcp_cmd.install_gemini_hooks(root, "cairn") == "already"
    data = json.loads((root / ".gemini" / "settings.json").read_text(encoding="utf-8"))
    assert len(data["hooks"]["BeforeAgent"]) == 1


def test_install_gemini_hooks_refuses_settings_it_cannot_parse(root):
    """Gemini CLI itself accepts comments in settings.json (checked live against
    0.61.0), so a commented file is real; rewriting it would drop them."""
    settings = root / ".gemini" / "settings.json"
    settings.parent.mkdir()
    text = '{\n  // my note\n  "mcpServers": {}\n}\n'
    settings.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        mcp_cmd.install_gemini_hooks(root, "cairn")
    assert settings.read_text(encoding="utf-8") == text


def test_hook_exe_leaves_a_plain_path_alone_and_flips_windows_slashes():
    assert mcp_cmd.hook_exe("cairn", "claude-code", windows=True) == "cairn"
    assert mcp_cmd.hook_exe("C:\\Py\\Scripts\\cairn.EXE", "claude-code", windows=True) == "C:/Py/Scripts/cairn.EXE"
    assert mcp_cmd.hook_exe("/usr/bin/cairn", "gemini-cli", windows=False) == "/usr/bin/cairn"


def test_hook_exe_quotes_a_spaced_path_for_the_shell_that_will_run_it():
    """Gemini CLI runs hooks through PowerShell on Windows (its own hook runner,
    0.61.0) and bash elsewhere; Claude Code through bash."""
    spaced = "C:\\Users\\First Last\\Scripts\\cairn.EXE"
    assert mcp_cmd.hook_exe(spaced, "claude-code", windows=True) == "'C:/Users/First Last/Scripts/cairn.EXE'"
    assert mcp_cmd.hook_exe(spaced, "gemini-cli", windows=True) == "& 'C:/Users/First Last/Scripts/cairn.EXE'"
    assert mcp_cmd.hook_exe("C:\\O'Neil x\\cairn.EXE", "gemini-cli", windows=True) == "& 'C:/O''Neil x/cairn.EXE'"
    assert mcp_cmd.hook_exe("/home/o'neil/bin/cairn", "gemini-cli", windows=False) == "'/home/o'\\''neil/bin/cairn'"


def test_hook_status_stays_consistent_for_a_path_that_needs_quoting(root):
    spaced = str(root / "a dir" / "cairn")
    mcp_cmd.install_gemini_hooks(root, spaced)
    assert mcp_cmd.gemini_hook_status(root, spaced) == {"BeforeAgent": True}
    assert mcp_cmd.install_gemini_hooks(root, spaced) == "already"


def test_gemini_hook_status_only_looks_at_geminis_own_settings_file(root):
    assert mcp_cmd.gemini_hook_status(root, "cairn") == {"BeforeAgent": False}
    mcp_cmd.install_claude_hooks(root, "cairn")
    assert mcp_cmd.gemini_hook_status(root, "cairn") == {"BeforeAgent": False}
    mcp_cmd.install_gemini_hooks(root, "cairn")
    assert mcp_cmd.gemini_hook_status(root, "cairn") == {"BeforeAgent": True}


# ---- cairn init wiring --------------------------------------------------

def test_init_with_claude_home_detected_and_yes_registers_everything(root):
    home = root / "home"
    (home / ".claude").mkdir(parents=True)
    project = root / "project"
    project.mkdir()
    out = _cli(project, "init", "--yes", env={"USERPROFILE": str(home), "HOME": str(home)})
    assert out.returncode == 0, out.stdout + out.stderr
    assert "Detected: claude-code" in out.stdout
    assert (project / ".mcp.json").is_file()
    assert (project / ".claude" / "settings.json").is_file()


def test_init_without_yes_and_declining_writes_nothing_beyond_cairn_dir(root):
    home = root / "home"
    (home / ".claude").mkdir(parents=True)
    project = root / "project"
    project.mkdir()
    out = _cli(project, "init", env={"USERPROFILE": str(home), "HOME": str(home)}, input="n\n")
    assert out.returncode == 0, out.stdout + out.stderr
    assert not (project / ".mcp.json").exists()
    assert not (project / ".claude").exists()


def test_init_with_no_hosts_detected_skips_the_section_cleanly(root):
    """No home-marker host must show up for an empty fake home. Gemini CLI is
    detected by real PATH, not the fake home, so it still legitimately shows
    up here if this machine happens to have it installed; account for that
    rather than asserting a blanket "nothing detected"."""
    home = root / "empty-home"
    home.mkdir()
    project = root / "project"
    project.mkdir()
    out = _cli(project, "init", "--yes", env={"USERPROFILE": str(home), "HOME": str(home)})
    assert out.returncode == 0, out.stdout + out.stderr
    if shutil.which("gemini"):
        assert "Detected: gemini-cli" in out.stdout
    else:
        assert "Detected:" not in out.stdout


def test_init_yes_registers_gemini_cli_mcp_hooks_and_agents_md(root):
    home = root / "home"
    home.mkdir()
    project = root / "project"
    project.mkdir()
    out = _cli(project, "init", "--yes", env=_env_with_fake_gemini(root, home))
    assert out.returncode == 0, out.stdout + out.stderr
    assert "Detected: gemini-cli" in out.stdout
    data = json.loads((project / ".gemini" / "settings.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["cairn"]["args"] == ["mcp", "serve"]
    group = data["hooks"]["BeforeAgent"][0]
    assert group["matcher"] == "*" and group["hooks"][0]["command"].endswith("memory detect")
    assert data["context"]["fileName"] == ["GEMINI.md", "AGENTS.md"]
    assert "GEMINI_CLI_TRUST_WORKSPACE" in out.stdout
    assert not (project / ".claude").exists() and not (project / ".mcp.json").exists()


def test_init_skips_a_gemini_settings_file_it_cannot_parse_instead_of_crashing(root):
    home = root / "home"
    home.mkdir()
    project = root / "project"
    (project / ".gemini").mkdir(parents=True)
    text = '{\n  // my note\n  "theme": "dark"\n}\n'
    (project / ".gemini" / "settings.json").write_text(text, encoding="utf-8")
    out = _cli(project, "init", "--yes", env=_env_with_fake_gemini(root, home))
    assert out.returncode == 0, out.stdout + out.stderr
    assert "gemini-cli: skipped" in out.stdout
    assert "cairn mcp config --host gemini-cli --with-hooks" in out.stdout
    assert (project / ".gemini" / "settings.json").read_text(encoding="utf-8") == text
    assert (project / ".cairn").is_dir()


def test_init_rerun_in_an_existing_project_still_offers_registration(root):
    """`cairn doctor` tells the user to run `cairn init` again to register, so
    that has to work after `.cairn/` exists (declined the first time here)."""
    home = root / "home"
    (home / ".claude").mkdir(parents=True)
    project = root / "project"
    project.mkdir()
    env = {"USERPROFILE": str(home), "HOME": str(home)}
    _cli(project, "init", env=env, input="n\n")
    assert not (project / ".mcp.json").exists()
    out = _cli(project, "init", "--yes", env=env)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "already exists" in out.stdout
    assert (project / ".mcp.json").is_file()
    assert (project / ".claude" / "settings.json").is_file()


def test_init_reports_other_detected_hosts_without_auto_registering(root):
    home = root / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".cursor").mkdir(parents=True)
    project = root / "project"
    project.mkdir()
    out = _cli(project, "init", "--yes", env={"USERPROFILE": str(home), "HOME": str(home)})
    assert out.returncode == 0, out.stdout + out.stderr
    assert "cursor detected: run `cairn mcp config --host cursor" in out.stdout
    assert not (project / ".cursor").exists()


# ---- cairn doctor reporting ---------------------------------------------

def test_doctor_reports_missing_then_ok_after_registration(root):
    home = root / "home"
    home.mkdir()
    project = root / "project"
    project.mkdir()
    _cli(project, "init", env={"USERPROFILE": str(home), "HOME": str(home)})
    before = _cli(project, "doctor", env={"USERPROFILE": str(home), "HOME": str(home)})
    assert "no .mcp.json" in before.stdout

    cairn_path = shutil.which("cairn") or "cairn"  # doctor compares against exactly this
    mcp_cmd.install_mcp_json(project, cairn_path)
    mcp_cmd.install_claude_hooks(project, cairn_path)
    after = _cli(project, "doctor", env={"USERPROFILE": str(home), "HOME": str(home)})
    assert ".mcp.json has cairn registered" in after.stdout
    assert "all 4 Cairn hooks present" in after.stdout


def test_doctor_reports_gemini_cli_mcp_and_hooks(root):
    home = root / "home"
    home.mkdir()
    project = root / "project"
    project.mkdir()
    env = _env_with_fake_gemini(root, home)
    _cli(project, "init", env=env)  # empty stdin: the registration prompt is declined
    before = _cli(project, "doctor", env=env)
    assert "no .gemini/settings.json" in before.stdout

    cairn_path = shutil.which("cairn") or "cairn"
    mcp_cmd.install_gemini_mcp_json(project, cairn_path)
    mcp_cmd.install_gemini_hooks(project, cairn_path)
    after = _cli(project, "doctor", env=env)
    assert ".gemini/settings.json has cairn registered" in after.stdout
    assert "BeforeAgent decision detector present" in after.stdout


def test_doctor_names_an_unparseable_gemini_settings_file_instead_of_guessing(root):
    home = root / "home"
    home.mkdir()
    project = root / "project"
    project.mkdir()
    env = {"USERPROFILE": str(home), "HOME": str(home)}
    _cli(project, "init", env=env)
    (project / ".gemini").mkdir()
    (project / ".gemini" / "settings.json").write_text('{\n  // note\n  "context": {}\n}\n', encoding="utf-8")
    out = _cli(project, "doctor", env=env)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "not plain JSON" in out.stdout
    assert "reads only GEMINI.md, not AGENTS.md" not in out.stdout
