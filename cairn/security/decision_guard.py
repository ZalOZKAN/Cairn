"""Prompt-injection guard for decisions.

Every active decision is written into AGENTS.md, which every agent reads as
instructions. That makes a decision record a path into every future session:
a web page or file an agent read could talk it into recording one, and a
hand-edited or pulled decision file is imported as-is. This module scans
decision text before it becomes active.

A flagged decision is not deleted and not dropped: it is stored inactive
("quarantined"), so it never reaches AGENTS.md or boot context, and waits
for a human to approve it with `cairn memory approve <id>`. Agents get no
tool to approve, so an injected agent cannot release its own text.

Pure regex, no model. It catches the common shapes (instruction overrides,
shell pipes, exfiltration, hidden characters), not every possible attack;
the human review step is the real boundary.
"""
from __future__ import annotations

import re

QUARANTINE_PREFIX = "quarantined: "

# Zero-width and bidirectional control characters: invisible in an editor,
# read by the model, and used to hide or reorder instructions.
_HIDDEN = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")

_RULES: list[tuple[str, str]] = [
    ("instruction override",
     r"(?:ignore|disregard|forget|override)\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier|system|other)"
     r"\s+(?:instructions?|prompts?|rules?|decisions?)"),
    ("instruction override", r"(?:önceki|yukarıdaki|tüm)\s+(?:talimatları|kuralları|komutları)\s+(?:yok\s+say|unut|görmezden\s+gel)"),
    ("instruction override", r"ignorez?\s+(?:les\s+|toutes\s+les\s+)?instructions\s+(?:précédentes|ci-dessus)"),
    ("instruction override", r"ignora\s+(?:las\s+|todas\s+las\s+)?instrucciones\s+(?:anteriores|previas)"),
    ("instruction override", r"ignoriere\s+(?:alle\s+)?(?:vorherigen|obigen)\s+anweisungen"),
    ("instruction override", r"忽略(?:之前|以上|所有)的?(?:指令|指示|规则)|無視して|以前の指示"),
    ("instruction override", r"игнорируй\s+(?:все\s+)?(?:предыдущие\s+)?инструкции"),
    ("role change", r"\byou\s+are\s+now\b|\bact\s+as\s+(?:an?\s+)?(?:system|admin|root)\b"),
    ("shell pipe", r"\b(?:curl|wget|iwr|invoke-webrequest)\b[^\n]*\|\s*(?:sh|bash|zsh|python\d?|iex|powershell)\b"),
    ("encoded command", r"\bpowershell(?:\.exe)?\s+[^\n]*-e(?:nc(?:odedcommand)?)?\s+[a-z0-9+/=]{16,}"),
    ("destructive command", r"\brm\s+-[a-z]*r[a-z]*f\b|\bdel\s+/[sq]\b|\bformat\s+[a-z]:|\bmkfs\b|\bdd\s+if="),
    # A secret named together with an outside destination, in either order.
    ("exfiltration",
     r"\b(?:secrets?|tokens?|passwords?|api[ _-]?keys?|credentials?|\.env|ssh\s+keys?|şifre|parola)\b[^\n]{0,80}"
     r"(?:https?://|\b[\w.-]+@[\w-]+\.[a-z]{2,}\b|webhook|pastebin|ngrok)"),
    ("exfiltration",
     r"(?:https?://|webhook|pastebin|ngrok)[^\n]{0,80}"
     r"\b(?:secrets?|tokens?|passwords?|api[ _-]?keys?|credentials?|\.env|ssh\s+keys?|şifre|parola)\b"),
    ("secret access", r"\b(?:cat|get-content)\s+[^\n]{0,40}(?:\.env\b|id_rsa|\.ssh/|credentials)"),
    ("markup", r"<!--|-->|<\s*/?\s*(?:system|instruction|prompt|script)\b"),
]
_COMPILED = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in _RULES]

MAX_DECISION_CHARS = 600
MAX_REASONING_CHARS = 4000


def scan(decision: str, reasoning: str | None = None, extra: list[str] | None = None) -> list[str]:
    """Reasons this decision looks like an injection attempt; empty when clean."""
    texts = [decision or "", reasoning or "", *(extra or [])]
    joined = "\n".join(texts)
    found: list[str] = []
    if _HIDDEN.search(joined):
        found.append("hidden characters")
    if len(decision or "") > MAX_DECISION_CHARS:
        found.append("decision text too long")
    if len(reasoning or "") > MAX_REASONING_CHARS:
        found.append("reasoning too long")
    for name, rx in _COMPILED:
        if rx.search(joined) and name not in found:
            found.append(name)
    return found


def decision_texts(d: dict) -> list[str]:
    """Alternatives and evidence of a decision dict, for `scan(extra=...)`."""
    out = []
    for a in d.get("alternatives") or []:
        if isinstance(a, dict):
            out += [str(a.get("option") or ""), str(a.get("rejected_because") or a.get("why_rejected") or "")]
    for e in d.get("evidence") or []:
        if isinstance(e, dict):
            out += [str(e.get("source") or ""), str(e.get("claim") or "")]
    return out
