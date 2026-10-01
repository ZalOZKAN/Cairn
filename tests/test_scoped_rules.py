"""Opt-in Claude Code delivery: repo-wide decisions in CLAUDE.md, each scoped
decision in a path-scoped rule file (`memory.agents_md.claude_scoped_rules`)."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from cairn.interface.commands.uninstall import uninstall
from cairn.memory import agents_md
from cairn.memory.memory import Memory

CLI = "import sys; sys.argv=['cairn', *{args!r}]; from cairn.interface.cli import main; main()"


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        r = Path(d)
        (r / ".cairn").mkdir()
        yield r


def _mem(root: Path, scoped: bool = True) -> Memory:
    cfg = {"async_store": False, "global_enabled": False, "agents_md": {"claude_scoped_rules": scoped}}
    return Memory({"memory": cfg}, project_dir=root)


def _decisions(m: Memory) -> dict[str, int]:
    return {
        "wide": m.record_decision("Use conventional commits", reasoning="clean history"),
        "api": m.record_decision("API errors use problem+json", reasoning="one error shape", scope_path="api"),
        "web": m.record_decision("Tokens live in httpOnly cookies", reasoning="XSS", scope_path="web/src"),
    }


def _rules(root: Path) -> dict[str, str]:
    d = root / agents_md.RULES_DIR
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(d.glob("*.md"))} if d.is_dir() else {}


def test_scoped_mode_splits_the_delivery(root):
    m = _mem(root)
    _decisions(m)
    result = agents_md.write(m, create=True)
    claude = (root / "CLAUDE.md").read_text(encoding="utf-8")
    agents = (root / "AGENTS.md").read_text(encoding="utf-8")
    assert "Use conventional commits" in claude and "problem+json" not in claude and "httpOnly" not in claude
    assert "@AGENTS.md" not in claude
    assert all(t in agents for t in ("conventional commits", "problem+json", "httpOnly"))
    rules = _rules(root)
    assert len(rules) == 2
    api = next(t for t in rules.values() if "problem+json" in t)
    assert api.startswith('---\npaths:\n  - "api"\n  - "api/**"\n---\n')
    assert "Why: one error shape" in api and "Use conventional commits" not in api
    assert any('  - "web/src/**"' in t for t in rules.values())
    assert "CLAUDE.md" in result["claude"] and len(result["claude"]) == 3
    assert not agents_md.stale(m)


def test_a_second_write_changes_nothing(root):
    m = _mem(root)
    _decisions(m)
    agents_md.write(m, create=True)
    assert "claude" not in agents_md.write(m)


def test_a_rule_file_follows_its_decisions(root):
    m = _mem(root)
    ids = _decisions(m)
    agents_md.write(m, create=True)
    m.invalidate(ids["web"])
    agents_md.write(m)
    rules = _rules(root)
    assert len(rules) == 1 and "problem+json" in next(iter(rules.values()))


def test_stale_notices_a_deleted_rule_file(root):
    m = _mem(root)
    _decisions(m)
    agents_md.write(m, create=True)
    next(iter((root / agents_md.RULES_DIR).glob("*.md"))).unlink()
    assert agents_md.stale(m)
    agents_md.write(m)
    assert not agents_md.stale(m)


def test_users_own_claude_md_text_and_rule_files_stay(root):
    (root / "CLAUDE.md").write_text("# Mine\n\nRun tests first.\n", encoding="utf-8")
    mine = root / agents_md.RULES_DIR
    mine.mkdir(parents=True)
    (mine / "mine.md").write_text("my own rule\n", encoding="utf-8")
    m = _mem(root)
    _decisions(m)
    agents_md.write(m, create=True)
    text = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith("# Mine\n\nRun tests first.\n") and "Use conventional commits" in text
    assert (mine / "mine.md").read_text(encoding="utf-8") == "my own rule\n"
    agents_md.write(_mem(root, scoped=False))
    assert (mine / "mine.md").exists() and len(_rules(root)) == 1


def test_switching_off_restores_the_import_and_removes_the_rules(root):
    (root / "CLAUDE.md").write_text("# Mine\n", encoding="utf-8")
    on = _mem(root)
    _decisions(on)
    agents_md.write(on, create=True)
    assert _rules(root)
    off = _mem(root, scoped=False)
    agents_md.write(off, create=True)
    text = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert "@AGENTS.md" in text and "httpOnly" not in text and text.startswith("# Mine\n")
    assert _rules(root) == {} and not (root / ".claude").exists()
    assert agents_md.link_claude(root) is None


def test_default_off_writes_no_rule_files_and_keeps_the_import(root):
    (root / "CLAUDE.md").write_text("# Mine\n", encoding="utf-8")
    m = _mem(root, scoped=False)
    _decisions(m)
    agents_md.write(m, create=True)
    agents_md.link_claude(root)
    assert _rules(root) == {}
    assert "@AGENTS.md" in (root / "CLAUDE.md").read_text(encoding="utf-8")


def test_link_claude_leaves_a_scoped_claude_md_alone(root):
    m = _mem(root)
    _decisions(m)
    agents_md.write(m, create=True)
    before = (root / "CLAUDE.md").read_text(encoding="utf-8")
    assert agents_md.link_claude(root) is None and agents_md.claude_link_target(root) is None
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == before


def test_uninstall_removes_both_parts_and_keeps_the_users_text(root):
    (root / "CLAUDE.md").write_text("# Mine\n", encoding="utf-8")
    m = _mem(root)
    _decisions(m)
    agents_md.write(m, create=True)
    plan = uninstall(root, apply=False)
    assert any(p == ".claude/rules/cairn/" for p, _ in plan.removed)
    assert _rules(root)
    uninstall(root, apply=True)
    assert (root / "CLAUDE.md").read_text(encoding="utf-8") == "# Mine\n"
    assert not (root / ".claude").exists() and not (root / "AGENTS.md").exists()


def test_uninstall_deletes_a_claude_md_cairn_created(root):
    m = _mem(root)
    _decisions(m)
    agents_md.write(m, create=True)
    uninstall(root, apply=True)
    assert not (root / "CLAUDE.md").exists()


def test_sync_and_doctor_report_the_mode(root):
    (root / ".cairn" / "config.yaml").write_text(
        "memory:\n  async_store: false\n  global_enabled: false\n  agents_md:\n    claude_scoped_rules: true\n",
        encoding="utf-8")
    _decisions(_mem(root))

    def cli(*args):
        return subprocess.run([sys.executable, "-c", CLI.format(args=list(args))], capture_output=True,
                              text=True, encoding="utf-8", cwd=root)

    out = cli("sync")
    assert out.returncode == 0 and "Claude Code scoped rules" in out.stdout
    assert cli("check").returncode == 0
    doctor = cli("doctor").stdout
    assert "scoped rules on" in doctor and "2 rule file(s)" in doctor
