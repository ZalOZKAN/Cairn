"""Corrections: "no, use X instead of Y" and "don't use Y" in a user message are decisions in the making.

Pure patterns for English and Turkish, no model. A match is only ever a proposal for the inbox (with the message as its
evidence and the rejected option already filled in), never a recorded decision.
"""
from __future__ import annotations

import re

_PIECE = r"(?:[^.,;:!?\n]|\.(?=\w)){2,60}?"       # a dot inside a word (moment.js) is part of the piece
_END = r"(?=[,;:!?\n]|\.(?!\w)|$)"

# Each pattern names the groups it finds: use (the choice), rej (what to drop).
PATTERNS = [
    re.compile(rf"\buse\s+(?P<use>{_PIECE})\s+instead\s+of\s+(?P<rej>{_PIECE}){_END}", re.I),
    re.compile(rf"\b(?:no|nope|wrong)\b[,.!]?\s+use\s+(?P<use>{_PIECE}){_END}", re.I),
    re.compile(rf"\b(?:do\s*n['’]?t|do\s+not|never|stop)\s+(?:use|using)\s+(?P<rej>{_PIECE}){_END}", re.I),
    re.compile(rf"(?P<rej>{_PIECE})\s+yerine\s+(?P<use>{_PIECE})\s+kullan\b", re.I),
    re.compile(rf"\bhay[ıi]r\b[,.!]?\s+(?P<use>{_PIECE})\s+kullan\b", re.I),
    re.compile(rf"(?P<rej>{_PIECE})\s+kullanma\b", re.I),
]
PASTE_LIMIT = 600           # a long message is a paste or a plan, not a correction


def detect_correction(prompt: str) -> dict | None:
    """{"text", "rejected", "evidence"} for a message that corrects the agent's choice, else None."""
    text = " ".join((prompt or "").split())
    if not text or len(text) > PASTE_LIMIT or text.startswith(("<", "[")):
        return None
    for pattern in PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        use = (m.groupdict().get("use") or "").strip(" '\"`")
        rej = (m.groupdict().get("rej") or "").strip(" '\"`")
        if not use and not rej:
            continue
        if len((use or rej).split()) > 8:
            continue                       # a sentence, not a choice
        if use and rej:
            decision = f"Use {use} instead of {rej}"
        elif use:
            decision = f"Use {use}"
        else:
            decision = f"Do not use {rej}"
        return {"text": decision, "rejected": [rej] if rej else [], "evidence": text[:300]}
    return None
