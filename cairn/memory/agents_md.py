"""Active project decisions compiled into one managed block of AGENTS.md.

AGENTS.md is the instruction file that Antigravity, Cursor, Codex and Copilot
load at session start, and (when the project has no CLAUDE.md) Claude Code
too, so one file carries the decisions to most agents. Cairn owns only the
text between the markers; the rest of the file is the user's.

Gemini CLI is the one exception: verified against a live install (0.61.0,
2026-09-27), it loads only `GEMINI.md` by default and never AGENTS.md unless
`context.fileName` in `.gemini/settings.json` explicitly lists it (see
`link_gemini`). An earlier version of this project claimed Gemini CLI read
AGENTS.md natively; it does not, and this is the fix.

Every active decision is written in full, nothing is truncated: measured on
the benchmark, a decision that is always in context is followed, one the
agent has to go and fetch is often skipped. The block is a pure function of
the decision records (ordered by importance, then uid), so it is identical on
every machine and produces clean git diffs. Staleness and the session handoff
are deliberately left out of it: both change constantly and would make a
committed file churn.

Two-way: an agent (or a human) can edit the block directly instead of calling
a tool, since every host can edit a file. `write()` compares the file's
bullets against the live decision records before regenerating: a decision
missing from the file was deleted by hand, so it is invalidated; a bullet with
no matching decision was added by hand, so it is recorded
(`recorded_by="agents.md"`); one of each in the same section is recorded as
superseding the other. No separate history of the file is kept: the one case
this cannot tell apart from a hand deletion on its own is a decision this very
call just inserted (the file, not yet regenerated, cannot show it yet), so
`Memory` passes those ids in as `exclude_ids`.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

FILENAME = "AGENTS.md"
BEGIN = "<!-- cairn:begin -->"
END = "<!-- cairn:end -->"
NOTICE = ("<!-- Managed by Cairn. To change a decision, edit a bullet below, or use record_decision / "
          "`cairn decide`; either way the next sync applies it and reformats this block. -->")
HANDOFF = ".cairn/persistence/session-handoff.md"

INSTRUCTIONS = (
    "Follow these decisions. If a task seems to require going against one, ask the user first.",
    "When a new decision is settled, even one only mentioned in passing, record it: MCP tool "
    '`record_decision`, `cairn decide "<decision>" --why "<reason>"`, or edit this block directly '
    "(add, remove, or change a bullet); the next sync reads your edit into the decision records.",
    f"Continuing earlier work, possibly started in another tool? Read `{HANDOFF}`. "
    "If you stop with the user's task unfinished, leave a note: MCP tool `memory_save`, or "
    '`cairn handoff "<what was done>" --next "<step still open>"`.',
)


def _one_line(text: str | None) -> str:
    return " ".join((text or "").split())


def _active_decisions(memory) -> list[dict]:
    from cairn.memory.memory import sqlite_connect
    with sqlite_connect(memory.db_path) as conn:
        rows = conn.execute(
            "SELECT id, prompt, reasoning, alternatives_json, scope_path, forbid_json FROM memories "
            "WHERE kind='decision' AND valid_until IS NULL ORDER BY importance DESC, uid ASC"
        ).fetchall()
    return [
        {"id": i, "decision": d, "reasoning": r, "alternatives": json.loads(a) if a else [], "scope": s,
         "forbid": json.loads(f) if f else []}
        for i, d, r, a, s, f in rows
    ]


def _texts_by_id(memory, ids: set[int]) -> dict[str | None, set[str]]:
    """Scope + decision text for specific rows, active or not: used to shield
    a row whose status this exact call just changed from being misread as a
    hand edit on either side of the diff (see `_import_hand_edits`)."""
    if not ids:
        return {}
    from cairn.memory.memory import sqlite_connect
    placeholders = ",".join("?" for _ in ids)
    with sqlite_connect(memory.db_path) as conn:
        rows = conn.execute(
            f"SELECT scope_path, prompt FROM memories WHERE id IN ({placeholders})", tuple(ids),
        ).fetchall()
    out: dict[str | None, set[str]] = {}
    for scope, prompt in rows:
        out.setdefault(scope, set()).add(_one_line(prompt))
    return out


def _entry(d: dict) -> list[str]:
    lines = [f"- **{_one_line(d['decision'])}**"]
    if d["reasoning"]:
        lines.append(f"  - Why: {_one_line(d['reasoning'])}")
    for a in d["alternatives"]:
        because = _one_line(a.get("rejected_because"))
        lines.append(f"  - Rejected: {_one_line(a.get('option'))}" + (f" ({because})" if because else ""))
    if d.get("forbid"):
        lines.append("  - Forbidden, an edit adding it is blocked: " + ", ".join(f"`{f}`" for f in d["forbid"]))
    return lines


def entries_by_id(memory) -> dict[int, list[str]]:
    """Each active decision's lines in the block's own format, by row id."""
    return {d["id"]: _entry(d) for d in _active_decisions(memory)}


def body(memory) -> str:
    """The block's content without markers; also used as boot context."""
    decisions = _active_decisions(memory)
    repo_wide = [d for d in decisions if not d["scope"]]
    by_scope: dict[str, list[dict]] = {}
    for d in decisions:
        if d["scope"]:
            by_scope.setdefault(d["scope"], []).append(d)

    lines = ["## Project decisions (Cairn)", "", *INSTRUCTIONS, ""]
    if not decisions:
        lines += ["No decisions recorded yet.", ""]
    if repo_wide:
        lines += ["### Repo-wide", ""]
        for d in repo_wide:
            lines += _entry(d)
        lines.append("")
    for scope, rows in sorted(by_scope.items()):
        lines += [f"### `{scope}/`", ""]
        for d in rows:
            lines += _entry(d)
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def block(memory) -> str:
    return f"{BEGIN}\n{NOTICE}\n{body(memory)}{END}"


_SCOPE_RE = re.compile(r"^### `(.+)/`\s*$")
_BULLET_RE = re.compile(r"^- \*\*(.+?)\*\*\s*$")
_WHY_RE = re.compile(r"^\s+- Why: (.*)$")
_REJECTED_RE = re.compile(r"^\s+- Rejected: ([^(]+?)(?: \((.*)\))?\s*$")


_NO_SECTION = object()  # before the first heading: instructions, not an entry


def _parse_body(text: str) -> dict[str | None, dict[str, dict]]:
    """Inverse of `body()`'s entries: {scope: {decision_text: entry}}. Only
    "### Repo-wide" / "### `scope/`" sections and `- **...**` bullets (plus
    their `Why:`/`Rejected:` sub-lines) are recognized; anything else (the
    instructions, prose a human added) is ignored rather than misread."""
    out: dict[str | None, dict[str, dict]] = {}
    scope = _NO_SECTION
    entry = None
    for line in text.split("\n"):
        if line.strip() == "### Repo-wide":
            scope, entry = None, None
            out.setdefault(scope, {})
            continue
        m = _SCOPE_RE.match(line)
        if m:
            scope, entry = m.group(1), None
            out.setdefault(scope, {})
            continue
        if scope is _NO_SECTION:
            continue
        m = _BULLET_RE.match(line)
        if m:
            entry = {"decision": _one_line(m.group(1)), "reasoning": None, "alternatives": []}
            out[scope][entry["decision"]] = entry
            continue
        if entry is None:
            continue
        m = _WHY_RE.match(line)
        if m:
            entry["reasoning"] = _one_line(m.group(1)) or None
            continue
        m = _REJECTED_RE.match(line)
        if m:
            entry["alternatives"].append(
                {"option": _one_line(m.group(1)), "rejected_because": _one_line(m.group(2))})
    return out


def _block_body(text: str) -> str | None:
    """Content between the markers, minus the notice line. None if no block."""
    start = text.find(BEGIN)
    if start == -1:
        return None
    end = text.find(END, start)
    if end == -1:
        return None
    inner = text[start + len(BEGIN):end].lstrip("\n")
    if inner.startswith(NOTICE):
        inner = inner[len(NOTICE):].lstrip("\n")
    return inner


def _import_hand_edits(memory, file_entries: dict, exclude_ids: set[int] | None = None) -> dict:
    """Diff the file's bullets directly against the live decision records (no
    separate history of the file is kept) and turn each difference into a
    real decision change: missing -> invalidate, new -> record, one of each
    in the same section -> supersede. `exclude_ids` are rows whose
    active/inactive status the caller just changed in this same call: the
    file (not yet regenerated) always lags one of these by construction, on
    whichever side of the diff it now falls, so neither must be mistaken for
    a hand edit. Set while running so `_refresh_agents_md` does not recurse
    into a mid-update regeneration of this same block."""
    active = _active_decisions(memory)
    by_scope: dict[str | None, dict[str, dict]] = {}
    for d in active:
        by_scope.setdefault(d["scope"], {})[_one_line(d["decision"])] = d
    skip = _texts_by_id(memory, exclude_ids or set())

    counts = {"invalidated": 0, "recorded": 0, "superseded": 0}
    memory._agents_md_syncing = True
    try:
        for scope in set(by_scope) | set(file_entries) | set(skip):
            db_texts = set(by_scope.get(scope, {}))
            file_texts = set(file_entries.get(scope, {}))
            ignore = skip.get(scope, set())
            removed = [by_scope[scope][t]["id"] for t in (db_texts - file_texts) - ignore]
            added = [file_entries[scope][t] for t in (file_texts - db_texts) - ignore]

            paired = min(len(removed), len(added))
            for old_id, entry in zip(removed[:paired], added[:paired]):
                memory.record_decision(
                    entry["decision"], reasoning=entry["reasoning"], alternatives=entry["alternatives"] or None,
                    scope_path=scope, supersedes=old_id, recorded_by="agents.md")
                counts["superseded"] += 1
            for old_id in removed[paired:]:
                if memory.invalidate(old_id, reason="removed from AGENTS.md by hand edit"):
                    counts["invalidated"] += 1
            for entry in added[paired:]:
                memory.record_decision(
                    entry["decision"], reasoning=entry["reasoning"], alternatives=entry["alternatives"] or None,
                    scope_path=scope, recorded_by="agents.md")
                counts["recorded"] += 1
    finally:
        memory._agents_md_syncing = False
    return counts


def path(project_dir: Path) -> Path:
    return project_dir / FILENAME


def _read(p: Path) -> tuple[str, str]:
    """(text with \\n newlines, the newline the file uses)."""
    raw = p.read_bytes().decode("utf-8") if p.exists() else ""
    return raw.replace("\r\n", "\n"), ("\r\n" if "\r\n" in raw else "\n")


def _replace_block(text: str, new_block: str) -> str | None:
    """`text` with its Cairn block replaced, or None when it has no block."""
    start = text.find(BEGIN)
    end = text.find(END, start + len(BEGIN)) if start != -1 else -1
    if start == -1 or end == -1:
        return None
    return text[:start] + new_block + text[end + len(END):]


def has_block(project_dir: Path) -> bool:
    text, _ = _read(path(project_dir))
    return _replace_block(text, "") is not None


CLAUDE_FILES = ("CLAUDE.md", ".claude/CLAUDE.md")

# Opt-in Claude Code delivery (`memory.agents_md.claude_scoped_rules`): the
# full block stays in AGENTS.md for every other host, while Claude Code, which
# reads CLAUDE.md instead of AGENTS.md, gets only the repo-wide decisions at
# launch and each scoped decision in a path-scoped rule file that the host
# loads when a matching file is read (code.claude.com/docs/en/memory). Measured
# on the bench (IMPROVEMENT_PLAN P13 R3): 68% fewer total tokens than a flat
# file at 1,000 decisions; no saving is claimed below a few hundred.
RULES_DIR = Path(".claude") / "rules" / "cairn"
CLAUDE_NOTICE = ("<!-- Managed by Cairn (memory.agents_md.claude_scoped_rules): repo-wide decisions only; "
                 "scoped ones are in .claude/rules/cairn/. Edit decisions in AGENTS.md or with `cairn decide`. -->")
RULE_NOTICE = ("<!-- Managed by Cairn: regenerated by `cairn sync`. Edit the decision in AGENTS.md or with "
               "`cairn decide`, not here. -->")
THIN_INSTRUCTIONS = (
    INSTRUCTIONS[0],
    "Decisions scoped to a directory are added to your context when you open or edit a file there.",
    "When a new decision is settled, even one only mentioned in passing, record it: MCP tool "
    '`record_decision`, or `cairn decide "<decision>" --why "<reason>"`.',
    INSTRUCTIONS[2],
)


def thin_body(memory) -> str:
    """What CLAUDE.md carries in scoped-rules mode: the instructions and the
    repo-wide decisions, in the AGENTS.md block's entry format."""
    repo_wide = [d for d in _active_decisions(memory) if not d["scope"]]
    lines = ["## Project decisions (Cairn)", "", *THIN_INSTRUCTIONS, ""]
    if repo_wide:
        lines += ["### Repo-wide", ""]
        for d in repo_wide:
            lines += _entry(d)
    return "\n".join(lines).rstrip("\n") + "\n"


def _rule_name(scope: str) -> str:
    import hashlib
    slug = re.sub(r"[^a-z0-9]+", "-", scope.lower()).strip("-")[:40] or "root"
    return f"{slug}-{hashlib.sha1(scope.encode('utf-8')).hexdigest()[:6]}.md"


def scope_rules(memory) -> dict[str, str]:
    """Rule file name -> text, one file per decision scope. A pure function of
    the decision records, so the files diff cleanly in git."""
    by_scope: dict[str, list[dict]] = {}
    for d in _active_decisions(memory):
        if d["scope"]:
            by_scope.setdefault(d["scope"].strip("/"), []).append(d)
    out = {}
    for scope, rows in sorted(by_scope.items()):
        lines = ["---", "paths:", f'  - "{scope}"', f'  - "{scope}/**"', "---", RULE_NOTICE, "",
                 f"## Project decisions (Cairn) for `{scope}/`", ""]
        for d in rows:
            lines += _entry(d)
        out[_rule_name(scope)] = "\n".join(lines) + "\n"
    return out


def _claude_file(project_dir: Path) -> Path:
    for rel in CLAUDE_FILES:
        if (project_dir / rel).exists():
            return project_dir / rel
    return project_dir / CLAUDE_FILES[0]


def _thin_block(memory) -> str:
    return f"{BEGIN}\n{CLAUDE_NOTICE}\n{thin_body(memory)}{END}"


def _has_thin_block(text: str) -> bool:
    return CLAUDE_NOTICE in text


def _managed_rule_files(project_dir: Path) -> list[Path]:
    d = project_dir / RULES_DIR
    if not d.is_dir():
        return []
    return [p for p in sorted(d.glob("*.md")) if RULE_NOTICE in p.read_text(encoding="utf-8", errors="replace")]


def _sync_claude_delivery(memory) -> list[str]:
    """Bring CLAUDE.md and the rule files in step with the mode; returns what
    changed (project-relative paths). Off: a thin block left by an earlier
    scoped run goes back to the `@AGENTS.md` import and the rule files go."""
    project_dir = memory.project_dir
    changed: list[str] = []
    claude = _claude_file(project_dir)
    text, newline = _read(claude)
    if memory.claude_scoped_rules:
        wanted = scope_rules(memory)
        updated = _replace_block(text, _thin_block(memory))
        if updated is None:
            updated = (text.rstrip("\n") + "\n\n" if text.strip() else "") + _thin_block(memory) + "\n"
    else:
        wanted = {}
        updated = None
        if _has_thin_block(text):
            target = "@AGENTS.md" if claude.name == "CLAUDE.md" and claude.parent == project_dir else "@../AGENTS.md"
            updated = _replace_block(text, f"{BEGIN}\n{target}\n{END}")
    if updated is not None and updated != text:
        claude.parent.mkdir(parents=True, exist_ok=True)
        claude.write_text(updated, encoding="utf-8", newline=newline)
        changed.append(claude.relative_to(project_dir).as_posix())

    rules_dir = project_dir / RULES_DIR
    for name, content in wanted.items():
        target_file = rules_dir / name
        if not target_file.exists() or target_file.read_text(encoding="utf-8") != content:
            rules_dir.mkdir(parents=True, exist_ok=True)
            target_file.write_text(content, encoding="utf-8", newline="\n")
            changed.append(target_file.relative_to(project_dir).as_posix())
    for old in _managed_rule_files(project_dir):
        if old.name not in wanted:
            old.unlink()
            changed.append(old.relative_to(project_dir).as_posix() + " (removed)")
    for d in (rules_dir, rules_dir.parent, rules_dir.parent.parent):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    return changed


def _scoped_stale(memory) -> bool:
    project_dir = memory.project_dir
    text, _ = _read(_claude_file(project_dir))
    if _replace_block(text, _thin_block(memory)) != text:
        return True
    wanted = scope_rules(memory)
    have = {p.name: p.read_text(encoding="utf-8") for p in _managed_rule_files(project_dir)}
    return have != wanted


def write(memory, create: bool = False, exclude_ids: set[int] | None = None) -> dict:
    """Bring the block in step with the decisions, both ways. Without
    `create`, a file that has no block is left alone (the user removed it, or
    never opted in). `exclude_ids`: see `_import_hand_edits`.
    Returns {"written", "invalidated", "recorded", "superseded"}, plus
    "claude" (the files the Claude Code delivery changed, see
    `_sync_claude_delivery`) when there were any."""
    p = path(memory.project_dir)
    text, newline = _read(p)
    existing_body = _block_body(text)
    if existing_body is None and not create:
        return {"written": False, "invalidated": 0, "recorded": 0, "superseded": 0}

    counts = {"invalidated": 0, "recorded": 0, "superseded": 0}
    if existing_body is not None:
        counts = _import_hand_edits(memory, _parse_body(existing_body), exclude_ids)

    new_block = block(memory)
    updated = _replace_block(text, new_block)
    if updated is None:
        updated = (text.rstrip("\n") + "\n\n" if text.strip() else "") + new_block + "\n"
    written = updated != text
    if written:
        p.write_text(updated, encoding="utf-8", newline=newline)
    claude = _sync_claude_delivery(memory)
    return {"written": written, **counts, **({"claude": claude} if claude else {})}


def stale(memory) -> bool:
    """True when the block is missing or differs from the decisions (and, in
    scoped-rules mode, when CLAUDE.md or a rule file does)."""
    text, _ = _read(path(memory.project_dir))
    updated = _replace_block(text, block(memory))
    if updated is None or updated != text:
        return True
    return memory.claude_scoped_rules and _scoped_stale(memory)


def _claude_link(project_dir: Path) -> tuple[Path, str] | None:
    """(the CLAUDE.md that needs an AGENTS.md import, the import line), or
    None when there is no CLAUDE.md or it already imports AGENTS.md."""
    for rel in CLAUDE_FILES:
        p = project_dir / rel
        if not p.exists():
            continue
        target = "@AGENTS.md" if rel == "CLAUDE.md" else "@../AGENTS.md"
        text = _read(p)[0]
        return None if target in text or _has_thin_block(text) else (p, target)
    return None


def claude_link_target(project_dir: Path) -> Path | None:
    link = _claude_link(project_dir)
    return link[0] if link else None


def link_claude(project_dir: Path) -> Path | None:
    """Claude Code reads AGENTS.md only when the project has no CLAUDE.md. If
    one exists, add an import of AGENTS.md to it (inside Cairn markers, once).
    Returns the file changed, or None when nothing was needed."""
    found = _claude_link(project_dir)
    if found is None:
        return None
    p, target = found
    text, newline = _read(p)
    link = f"{BEGIN}\n{target}\n{END}"
    updated = _replace_block(text, link)
    if updated is None:
        updated = (text.rstrip("\n") + "\n\n" if text.strip() else "") + link + "\n"
    p.write_text(updated, encoding="utf-8", newline=newline)
    return p


WIRING = Path(".cairn") / "persistence" / "wiring.json"
GEMINI_LINK = "gemini-cli context.fileName AGENTS.md"


def _wiring(project_dir: Path) -> set[str]:
    try:
        data = json.loads((project_dir / WIRING).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    added = data.get("added") if isinstance(data, dict) else None
    return set(added) if isinstance(added, list) else set()


def wired(project_dir: Path, key: str) -> bool:
    return key in _wiring(project_dir)


def record_wiring(project_dir: Path, key: str, added: bool) -> None:
    """Remember (or forget) a setting Cairn added that nothing inside the
    setting itself marks as Cairn's, so `cairn uninstall` can tell it from one
    the user wrote. Per machine (gitignored): without the record, uninstall
    keeps the setting."""
    p = project_dir / WIRING
    if not p.parent.is_dir():
        return
    keys = _wiring(project_dir)
    keys = keys | {key} if added else keys - {key}
    p.write_text(json.dumps({"added": sorted(keys)}, indent=2) + "\n", encoding="utf-8")


def gemini_in_use(project_dir: Path) -> bool:
    return (project_dir / ".gemini" / "settings.json").is_file() or (project_dir / "GEMINI.md").is_file()


def merge_gemini_context(data: dict) -> bool:
    """AGENTS.md appended to `context.fileName` (GEMINI.md, the default, kept
    first). False when it is already listed."""
    context = data.setdefault("context", {})
    names = context.get("fileName") or ["GEMINI.md"]
    if isinstance(names, str):
        names = [names]
    if "AGENTS.md" in names:
        return False
    context["fileName"] = [*names, "AGENTS.md"]
    return True


def link_gemini(project_dir: Path) -> Path | None:
    """Gemini CLI reads only `GEMINI.md` by default (verified against a live
    install, 0.61.0, 2026-09-27); AGENTS.md is never loaded unless
    `context.fileName` in `.gemini/settings.json` lists it. Only acts when
    the project already shows Gemini CLI in use (a `GEMINI.md` or a
    `.gemini/settings.json` already there), the same restraint `link_claude`
    has for CLAUDE.md: never create `.gemini/` out of nothing for a project
    that has never touched Gemini CLI. Merges into the file (every other
    setting kept) rather than overwriting it. Returns the file changed, or
    None when nothing was needed or the file exists but does not parse as
    JSON (never clobbered)."""
    path = project_dir / ".gemini" / "settings.json"
    if not gemini_in_use(project_dir):
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except json.JSONDecodeError:
        return None
    if not merge_gemini_context(data):
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    record_wiring(project_dir, GEMINI_LINK, added=True)
    return path
