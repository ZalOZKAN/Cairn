"""Decision enforcement: code added under a decision's scope must not match
the decision's `forbid` regexes.

Only added lines are checked, never code that was already there: a legacy file
a decision says to leave alone keeps its old pattern, and editing near it is
not a violation. Cairn's own files and the agent instruction files are not
checked either, since describing a rule there is not breaking it.
"""
from __future__ import annotations

import re
import subprocess
from collections import Counter
from pathlib import Path

_EXEMPT_NAMES = {"AGENTS.md", "CLAUDE.md", "GEMINI.md"}


def exempt(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    return bool(parts) and (parts[0] == ".cairn" or parts[-1] in _EXEMPT_NAMES)


def added_lines(old: str, new: str) -> list[str]:
    """Lines of `new` that `old` does not already have, counted as a multiset."""
    have = Counter(old.splitlines())
    out = []
    for line in new.splitlines():
        if have[line]:
            have[line] -= 1
        else:
            out.append(line)
    return out


def edit_from_hook(payload: dict, project_dir: Path) -> tuple[str, list[str]] | None:
    """(file path, added lines) from a Claude Code PreToolUse or Gemini CLI
    BeforeTool payload for a file edit, None for anything else. Both hosts
    name the fields the same way: Write/write_file carry `content`,
    Edit/replace carry `old_string`/`new_string`, MultiEdit carries `edits`."""
    ti = payload.get("tool_input") or {}
    path = ti.get("file_path") or ""
    if not isinstance(path, str) or not path:
        return None
    if isinstance(ti.get("edits"), list):
        lines = [x for e in ti["edits"] if isinstance(e, dict)
                 for x in added_lines(str(e.get("old_string") or ""), str(e.get("new_string") or ""))]
    elif "new_string" in ti:
        lines = added_lines(str(ti.get("old_string") or ""), str(ti["new_string"] or ""))
    elif "content" in ti:
        target = Path(path) if Path(path).is_absolute() else project_dir / path
        try:
            old = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            old = ""
        lines = added_lines(old, str(ti["content"] or ""))
    else:
        return None
    return path, lines


def violations(memory, path: str, lines: list[str]) -> list[dict] | None:
    """One entry per (decision, pattern) that an added line matches, with the
    first matching line. None when no decision with `forbid` applies to
    `path`, so nothing was checked."""
    if Path(path).is_absolute():
        try:
            Path(path).resolve().relative_to(Path(memory.project_dir).resolve())
        except ValueError:
            return None
    rel = memory._relative_to_project(path)
    if not lines or not rel or exempt(rel):
        return None
    rules = memory.forbidden_for_path(rel)
    if not rules:
        return None
    found = []
    for d in rules:
        for pattern in d["forbid"]:
            rx = re.compile(pattern)
            hit = next((line for line in lines if rx.search(line)), None)
            if hit is not None:
                found.append({**d, "pattern": pattern, "line": hit.strip(), "file": rel})
    return found


def block_message(found: list[dict],
                  lead: str = "Blocked by Cairn: this edit adds code that a recorded decision forbids.") -> str:
    out = [lead]
    for v in found:
        out.append(f"- decision #{v['id']}: {v['decision']}")
        if v.get("reasoning"):
            out.append(f"  why: {v['reasoning']}")
        out.append(f"  forbidden pattern: {v['pattern']}  (in {v['file']}: {v['line']})")
    out.append("Rewrite the change so it follows the decision. Only if the user has said this decision "
               "no longer holds, record the new one first (record_decision with supersedes=<id>); "
               "never supersede a decision just to get past this block.")
    return "\n".join(out)


def _git(project_dir: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-c", "core.quotepath=off", *args], cwd=project_dir,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def diff_added_lines(project_dir: Path, spec: str = "", staged: bool = False) -> dict[str, list[str]] | None:
    """{file: added lines} for a git diff: `spec` (a revision or range),
    `staged` (the index), or neither (all uncommitted work, untracked files
    counted whole). Paths are relative to `project_dir`. None when git could
    not produce the diff."""
    args = ["diff", "-U0", "--no-color", "--no-ext-diff", "--relative"]
    if staged:
        args.append("--cached")
    elif spec:
        args.append(spec)
    else:
        args.append("HEAD")
    out = _git(project_dir, *args)
    if out is None and not spec and not staged:
        out = _git(project_dir, "diff", "-U0", "--no-color", "--no-ext-diff", "--relative")
    if out is None:
        return None
    added: dict[str, list[str]] = {}
    current = None
    for line in out.splitlines():
        if line.startswith("+++ "):
            target = line[4:]
            current = None if target == "/dev/null" else target.removeprefix("b/")
        elif line.startswith("+") and current is not None:
            added.setdefault(current, []).append(line[1:])
    if not spec and not staged:
        untracked = _git(project_dir, "ls-files", "--others", "--exclude-standard", "-z")
        for rel in (untracked or "").split("\0"):
            if rel:
                try:
                    added[rel] = (project_dir / rel).read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    pass
    return added
