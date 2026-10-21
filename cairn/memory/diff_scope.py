"""Which active decisions govern the files a git diff touches.

`decisions_for_path` answers this for one file, which is what a per-edit hook
uses; a host without such a hook (or a human, or CI, before a commit or in a
review) needs it for the whole change at once. Pure git plus the decision
records: no model call, and repo-wide decisions stay out for the same reason
they do in `decisions_for_path` (AGENTS.md and the session context already carry
them).
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git(project_dir: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=project_dir, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _names(project_dir: Path, *args: str) -> list[str] | None:
    """A git command's file names. `-z` keeps unusual names unquoted, so the
    output is split on NUL."""
    out = _git(project_dir, *args, "-z")
    return None if out is None else [p for p in out.split("\0") if p]


def changed_paths(project_dir: Path, spec: str = "", staged: bool = False) -> list[str] | None:
    """Files changed, relative to `project_dir` (a monorepo subdirectory sees
    only its own): `spec` is any `git diff` revision or range (`main..HEAD`,
    `HEAD~3`); `staged` limits it to the index; neither means all uncommitted
    work, including files git does not track yet. Cairn's own `.cairn/` files
    are left out (a recorded decision is itself an uncommitted change, and no
    decision governs them). Renames count as both the old and the new path: a
    decision scoped to the directory a file left still applies. None when git
    could not produce the list (not a repository, unknown revision, or a
    `spec` that would be read as an option)."""
    if spec.startswith("-"):
        return None
    if spec:
        paths = _names(project_dir, "diff", "--name-only", "--no-renames", "--relative", spec)
    elif staged:
        paths = _names(project_dir, "diff", "--name-only", "--no-renames", "--relative", "--cached")
    else:
        has_head = _git(project_dir, "rev-parse", "--verify", "-q", "HEAD") is not None
        tracked = _names(project_dir, "diff", "--name-only", "--no-renames", "--relative",
                         "HEAD" if has_head else "--cached")
        untracked = _names(project_dir, "ls-files", "--others", "--exclude-standard")
        paths = None if tracked is None or untracked is None else [*tracked, *untracked]
    if paths is None:
        return None
    return sorted({p for p in paths if not p.startswith(".cairn/")})


def decisions_for_diff(memory, paths: list[str]) -> list[dict]:
    """Each active path-scoped decision that applies to at least one of
    `paths`, once, with the changed files it applies to under `files`; most
    important first, then newest."""
    found: dict[str, dict] = {}
    for path in paths:
        for row in memory.decisions_for_path(path):
            found.setdefault(row["uid"], {**row, "files": []})["files"].append(path)
    return sorted(found.values(), key=lambda r: (-(r.get("importance") or 0), -r["id"]))
