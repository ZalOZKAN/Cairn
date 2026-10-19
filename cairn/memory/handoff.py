"""Cross-tool session handoff: `.cairn/persistence/session-handoff.md`.

Switching from one agent to another (Antigravity to Claude Code to Cursor)
loses the conversation, so the next agent needs two things on disk: the
previous agent's own note (what was done, what comes next) and facts nobody
has to remember to write down. The note comes from `memory_save` /
`cairn handoff`; the facts (working tree, latest decisions) are read from git
and the decision records every time the handoff is written or refreshed, so
they are there even when the previous agent left no note.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

DECISIONS_HEADER = "## Recent Decisions"
TREE_HEADER = "## Working Tree"
RECENT_DECISIONS = 5
MAX_CHANGED_FILES = 30


def _git(project_dir: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=project_dir, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.rstrip("\n") if out.returncode == 0 else None


def working_tree(project_dir: Path) -> str:
    if _git(project_dir, "rev-parse", "--is-inside-work-tree") != "true":
        return "Not a git repository."
    branch = _git(project_dir, "branch", "--show-current")
    lines = [f"Branch: {branch}" if branch else "Branch: (detached HEAD)"]
    status = [
        ln for ln in (_git(project_dir, "status", "--porcelain", "--untracked-files=all") or "").splitlines()
        if ln[3:] and not ln[3:].startswith(".cairn/")
    ]
    if status:
        lines.append("Uncommitted changes:")
        lines += [f"- `{ln[:2].strip()}` {ln[3:]}" for ln in status[:MAX_CHANGED_FILES]]
        if len(status) > MAX_CHANGED_FILES:
            lines.append(f"- ... and {len(status) - MAX_CHANGED_FILES} more (run `git status`)")
    else:
        lines.append("No uncommitted changes.")
    log = _git(project_dir, "log", "-3", "--format=%h %s (%cr)")
    if log:
        lines.append("Last commits:")
        lines += [f"- {ln}" for ln in log.splitlines()]
    return "\n".join(lines)


def recent_decisions(memory, n: int = RECENT_DECISIONS) -> str:
    from cairn.memory.memory import sqlite_connect
    with sqlite_connect(memory.db_path) as conn:
        rows = conn.execute(
            "SELECT timestamp, prompt, scope_path, recorded_by FROM memories "
            "WHERE kind='decision' AND valid_until IS NULL ORDER BY timestamp DESC, uid DESC LIMIT ?",
            (n,),
        ).fetchall()
    if not rows:
        return "None recorded yet."
    out = []
    for ts, decision, scope, by in rows:
        where = f" [{scope}]" if scope else ""
        via = f" (via {by})" if by else ""
        first = (decision or "").strip().splitlines()[0]
        out.append(f"- {ts[:10]}{where} {first}{via}")
    out.append("All active decisions are in AGENTS.md.")
    return "\n".join(out)


def _replace_section(text: str, header: str, content: str) -> str:
    """Replace a `## ` section (header line up to the next `## ` or EOF), or
    append it when missing. Other sections are kept verbatim."""
    new = f"{header}\n{content}\n"
    lines = text.split("\n")
    try:
        start = lines.index(header)
    except ValueError:
        return text.rstrip("\n") + ("\n\n" if text.strip() else "") + new
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    tail = "\n".join(lines[end:])
    return "\n".join(lines[:start]) + ("\n" if start else "") + new + ("\n" + tail if tail else "")


def refresh(memory, handoff_path: Path) -> Path:
    """Rewrite the git and decision sections of the handoff, keeping the
    previous agent's note. Creates a minimal handoff when there is none."""
    text = handoff_path.read_text(encoding="utf-8") if handoff_path.exists() else ""
    if not text.strip():
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        text = f"# Session Handoff: {now}\n\nNo note was left by the previous session.\n"
    text = _replace_section(text, DECISIONS_HEADER, recent_decisions(memory))
    text = _replace_section(text, TREE_HEADER, working_tree(memory.project_dir))
    handoff_path.parent.mkdir(parents=True, exist_ok=True)
    handoff_path.write_text(text.rstrip("\n") + "\n", encoding="utf-8")
    return handoff_path


def save_note(project, summary: str = "", next_step: str = "", source: str | None = None) -> Path:
    """Write the agent's note as the handoff, then add the git and decision
    sections. `source` is the tool that left it (MCP client name, "cli")."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    signed = summary.strip()
    if source:
        signed = (signed + "\n\n" if signed else "") + f"_Left by {source} at {now}._"
    extraction = {"next_session_start": next_step.strip()} if next_step.strip() else {}
    project.session.end_session(extraction, signed)
    return refresh(project.memory, project.session.handoff_path)
