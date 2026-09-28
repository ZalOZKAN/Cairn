"""Decision records as git-native Markdown files (`.cairn/decisions/*.md`).

One file per decision, ADR-shaped, YAML front matter + fixed sections. The
files are the source of truth; the SQLite rows are a rebuildable index. One
file per decision means two people recording decisions never touch the same
file, so git merges stay trivial.
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

DECISIONS_DIRNAME = "decisions"

_FRONT_KEYS = (
    "uid", "date", "status", "importance", "path", "supersedes",
    "superseded_by", "valid_until", "invalidated_reason", "anchor_commit", "recorded_by",
)
_ITEM_RE = re.compile(r"^- \*\*(.+?)\*\*:\s?(.*)$")


def new_uid(now: datetime) -> str:
    return f"{now.strftime('%Y%m%d')}-{secrets.token_hex(3)}"


def slugify(text: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "decision"


def find_file(directory: Path, uid: str) -> Path | None:
    matches = sorted(directory.glob(f"{uid}-*.md")) + sorted(directory.glob(f"{uid}.md"))
    return matches[0] if matches else None


def render(d: dict[str, Any]) -> str:
    front = {k: d.get(k) for k in _FRONT_KEYS if d.get(k) not in (None, "", [])}
    lines = ["---", yaml.safe_dump(front, sort_keys=False, allow_unicode=True).rstrip(), "---", ""]
    title = (d.get("decision") or "").strip().splitlines()[0] if d.get("decision") else "Decision"
    lines += [f"# {title}", "", "## Decision", "", (d.get("decision") or "").strip(), ""]
    if d.get("reasoning"):
        lines += ["## Reasoning", "", d["reasoning"].strip(), ""]
    if d.get("chose_because"):
        lines += ["## Chose because", "", d["chose_because"].strip(), ""]
    if d.get("alternatives"):
        lines += ["## Alternatives", ""]
        lines += [
            f"- **{_one_line(a.get('option'))}**: {_one_line(a.get('rejected_because'))}"
            for a in d["alternatives"]
        ]
        lines.append("")
    if d.get("evidence"):
        lines += ["## Evidence", ""]
        lines += [
            f"- **{_one_line(e.get('source'))}**: {_one_line(e.get('claim'))}"
            for e in d["evidence"]
        ]
        lines.append("")
    return "\n".join(lines)


def parse(text: str) -> dict[str, Any]:
    """Inverse of `render`. Tolerates hand edits: unknown sections are ignored,
    a missing Decision section falls back to the `# title` line."""
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise ValueError("missing YAML front matter")
    end = text.find("\n---", 4)
    if end == -1:
        raise ValueError("unterminated YAML front matter")
    front = yaml.safe_load(text[4:end]) or {}
    if not isinstance(front, dict) or not front.get("uid"):
        raise ValueError("front matter needs a uid")
    body = text[end + 4:]

    out: dict[str, Any] = {k: front.get(k) for k in _FRONT_KEYS}
    out["uid"] = str(front["uid"])
    for k in ("supersedes", "superseded_by", "anchor_commit", "recorded_by"):
        if out[k] is not None:
            out[k] = str(out[k])
    if out["date"] is not None and not isinstance(out["date"], str):
        out["date"] = out["date"].isoformat()
    if out["valid_until"] is not None and not isinstance(out["valid_until"], str):
        out["valid_until"] = out["valid_until"].isoformat()
    out["importance"] = int(front.get("importance") or 2)
    out["status"] = front.get("status") or "active"

    sections: dict[str, str] = {}
    title = ""
    current = None
    buf: list[str] = []
    for line in body.split("\n"):
        if line.startswith("# ") and not title and current is None:
            title = line[2:].strip()
            continue
        if line.startswith("## "):
            if current:
                sections[current] = "\n".join(buf).strip()
            current, buf = line[3:].strip(), []
            continue
        if current:
            buf.append(line)
    if current:
        sections[current] = "\n".join(buf).strip()

    out["decision"] = sections.get("Decision") or title
    out["reasoning"] = sections.get("Reasoning") or None
    out["chose_because"] = sections.get("Chose because") or None
    out["alternatives"] = [
        {"option": o, "rejected_because": r} for o, r in _items(sections.get("Alternatives", ""))
    ]
    out["evidence"] = [{"source": s, "claim": c} for s, c in _items(sections.get("Evidence", ""))]
    if not out["decision"]:
        raise ValueError("decision text is empty")
    return out


def _items(block: str) -> list[tuple[str, str]]:
    items = []
    for line in block.splitlines():
        m = _ITEM_RE.match(line.strip())
        if m:
            items.append((m.group(1).strip(), m.group(2).strip()))
    return items


def _one_line(text: str | None) -> str:
    return " ".join((text or "").split())


_ADR_STATUS = {
    "superseded": "superseded", "deprecated": "invalidated", "rejected": "invalidated",
}


def adr_to_decision(text: str, source_name: str, today: datetime) -> dict[str, Any] | None:
    """Best-effort mapping of a Nygard or MADR record onto a decision dict.
    Returns None for proposed/draft ADRs (not decided yet). The uid is derived
    from the source file name, so re-importing the same ADR is a no-op."""
    import hashlib

    text = text.replace("\r\n", "\n")
    title = ""
    sections: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for line in text.split("\n"):
        if line.startswith("# ") and not title:
            title = re.sub(r"^\d+[.:)]?\s*", "", line[2:].strip())
            continue
        if line.startswith("## "):
            if current:
                sections[current] = "\n".join(buf).strip()
            current, buf = line[3:].strip().lower(), []
            continue
        if current:
            buf.append(line)
    if current:
        sections[current] = "\n".join(buf).strip()
    if not title:
        return None

    status_text = (sections.get("status") or "").lower()
    m = re.search(r"^\s*(?:\*\s*)?status:\s*(\w+)", text, re.IGNORECASE | re.MULTILINE)
    if m:
        status_text = m.group(1).lower()
    if any(w in status_text for w in ("proposed", "draft")):
        return None
    status = next((v for k, v in _ADR_STATUS.items() if k in status_text), "active")

    date_m = re.search(r"(\d{4}-\d{2}-\d{2})", sections.get("date", "") + "\n" + text[:600])
    date = date_m.group(1) if date_m else today.strftime("%Y-%m-%d")
    uid = f"{date.replace('-', '')}-{hashlib.sha1(source_name.encode()).hexdigest()[:6]}"

    decision = sections.get("decision") or sections.get("decision outcome") or title
    options = sections.get("considered options", "")
    alternatives = [
        {"option": o.strip(), "rejected_because": "not chosen (imported from ADR)"}
        for o in re.findall(r"^\s*[-*]\s+(.+)$", options, re.MULTILINE)
        if o.strip() and o.strip().lower() not in decision.lower()
    ]
    reasoning = "\n\n".join(
        s for s in (sections.get("context"), sections.get("context and problem statement"),
                    sections.get("consequences")) if s
    ) or None
    return {
        "uid": uid, "date": f"{date}T00:00:00+00:00", "status": status, "importance": 2,
        "decision": f"{title}\n\n{decision}" if decision != title else title,
        "reasoning": reasoning, "alternatives": alternatives,
        "evidence": [{"source": "ADR", "claim": source_name}],
    }


def format_brief(rows: list[dict], header: str) -> str:
    """One line per decision; used in boot context, MCP output and hooks."""
    if not rows:
        return ""
    lines = [header]
    for r in rows:
        where = f" [{r['path']}]" if r.get("path") else ""
        flag = ""
        if r.get("stale"):
            flag = f" (NEEDS REVIEW: {r['stale']['commits_since']} commits since recorded)"
        first = (r.get("decision") or "").strip().splitlines()[0] if r.get("decision") else ""
        files = r.get("files") or []
        touches = ""
        if files:
            shown = ", ".join(files[:3]) + (f", +{len(files) - 3} more" if len(files) > 3 else "")
            touches = f" (changed: {shown})"
        lines.append(f"- #{r['id']}{where}: {first}{flag}{touches}")
    return "\n".join(lines)
