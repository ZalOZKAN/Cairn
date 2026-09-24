"""
Conflict detection, opt-in.

Does a record about to be stored contradict a prior decision? Ask the LLM.
Adversary warning: false-positive rate can be high. Therefore:
- Only runs when explicitly enabled in config.
- Result is returned as a "user warning", it does NOT auto-invalidate.
- Only runs against high-importance (≥2) records (noise reduction).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cairn.memory.memory import Memory


SYSTEM_PROMPT = """You are a memory conflict detector.
Given a NEW memory entry and a list of PRIOR entries, identify any PRIOR entry whose stated
decision/fact directly contradicts the NEW one. Be strict: only flag direct contradictions
(e.g. "use postgres" vs "use sqlite"), not topic overlap.

Return strict JSON: {"conflicts": [{"id": <id from the PRIOR list, exactly as shown, a plain
number or a "g<number>" string>, "reason": "<one short sentence>"}]}
If none, return {"conflicts": []}."""


class ConflictDetector:
    def __init__(self, config: dict, model: str | None = None):
        cfg = config.get("memory", {}).get("conflict", {})
        self.enabled = bool(cfg.get("enabled", False))
        self.model = model or cfg.get("model", "groq/llama-3.3-70b-versatile")
        self.min_importance = int(cfg.get("min_importance", 2))
        self.scan_limit = int(cfg.get("scan_limit", 20))

    def detect(self, memory: "Memory", new_prompt: str, new_response: str) -> list[dict]:
        """Return a list of {id, reason} for conflicting prior records. Empty list if none."""
        if not self.enabled:
            return []
        try:
            import litellm
        except ImportError:
            return []

        priors = self._candidates(memory)
        if not priors:
            return []

        user_prompt = self._format_user(new_prompt, new_response, priors)
        try:
            resp = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=500,
            )
            text = resp.choices[0].message.content or ""
            return self._parse(text)
        except Exception:
            return []

    def _candidates(self, memory: "Memory") -> list[tuple[int | str, str, str]]:
        """Recent high-importance rows from the project DB, plus the global
        DB (tagged "g<id>") when global memory is enabled, a contradiction
        can be against a cross-project preference just as much as a local one."""
        from cairn.memory.memory import sqlite_connect

        def _scan(db_path) -> list[tuple[int, str, str]]:
            with sqlite_connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT id, prompt, response FROM memories "
                    "WHERE valid_until IS NULL AND importance >= ? "
                    "ORDER BY id DESC LIMIT ?",
                    (self.min_importance, self.scan_limit),
                ).fetchall()
            return [(r[0], r[1], r[2]) for r in rows]

        out: list[tuple[int | str, str, str]] = list(_scan(memory.db_path))
        if getattr(memory, "global_enabled", False):
            out.extend((f"g{rid}", p, r) for rid, p, r in _scan(memory.global_db_path))
        return out

    @staticmethod
    def _format_user(new_prompt: str, new_response: str, priors: list[tuple[int | str, str, str]]) -> str:
        lines = [f"NEW:\nQ: {new_prompt}\nA: {new_response[:400]}\n\nPRIOR:"]
        for rid, p, r in priors:
            lines.append(f"[{rid}] Q: {p[:120]} / A: {r[:200]}")
        return "\n".join(lines)

    @staticmethod
    def _parse(text: str) -> list[dict]:
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1:
                return []
            data = json.loads(text[start : end + 1])
            out = []
            for c in data.get("conflicts", []):
                rid = c.get("id")
                reason = c.get("reason", "")
                if isinstance(rid, str) and rid.isdigit():
                    rid = int(rid)
                is_global_id = isinstance(rid, str) and rid.startswith("g") and rid[1:].isdigit()
                if isinstance(rid, int) or is_global_id:
                    out.append({"id": rid, "reason": reason})
            return out
        except (json.JSONDecodeError, AttributeError, TypeError):
            return []
