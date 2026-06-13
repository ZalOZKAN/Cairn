"""
Memory deduplication, merge near-duplicate records.

Strategies:
1. Hash dedup (always on): if sha1(prompt+response) matches, bump importance
   on the existing row instead of inserting.
2. Semantic dedup (when ChromaDB is available): if cosine similarity ≥
   threshold, strengthen the existing record.

Both the semantic beta path and the basic hash check live here.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cairn.memory.memory import Memory


DEFAULT_THRESHOLD = 0.92


def _bump_existing(memory: "Memory", row_id: int) -> None:
    with sqlite3.connect(memory.db_path) as conn:
        conn.execute(
            "UPDATE memories SET importance = importance + 1, timestamp = ? "
            "WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), row_id),
        )
        conn.commit()


def maybe_merge(memory: "Memory", entry: dict) -> bool:
    """
    Returns True if the record was MERGED into an existing row (caller must
    NOT insert). Returns False if no merge happened; caller may insert.
    """
    cfg = getattr(memory, "_dedup_cfg", None)
    if cfg is None:
        cfg = {"enabled": True, "threshold": DEFAULT_THRESHOLD}

    if not cfg.get("enabled", True):
        return False

    # 1) Hash match, exact same content
    h = entry.get("content_hash")
    if h:
        with sqlite3.connect(memory.db_path) as conn:
            row = conn.execute(
                "SELECT id FROM memories WHERE content_hash = ? AND valid_until IS NULL "
                "ORDER BY id DESC LIMIT 1",
                (h,),
            ).fetchone()
        if row:
            _bump_existing(memory, row[0])
            return True

    # 2) Semantic dedup, if Chroma is available, check cosine similarity
    chroma = getattr(memory, "_chroma", None)
    threshold = float(cfg.get("threshold", DEFAULT_THRESHOLD))
    if chroma is not None:
        try:
            res = chroma.query(
                query_texts=[f"{entry['prompt']}\n{entry['response'][:500]}"],
                n_results=1,
            )
            ids = (res.get("ids") or [[]])[0]
            distances = (res.get("distances") or [[]])[0]
            if ids and distances:
                # Chroma cosine: distance ∈ [0, 2]; similarity = 1 - distance/2
                similarity = 1.0 - float(distances[0]) / 2.0
                if similarity >= threshold:
                    try:
                        _bump_existing(memory, int(ids[0]))
                        return True
                    except (TypeError, ValueError):
                        return False
        except Exception:
            return False

    return False


def configure(memory: "Memory", enabled: bool = True, threshold: float = DEFAULT_THRESHOLD) -> None:
    """Configure dedup settings on a Memory instance."""
    memory._dedup_cfg = {"enabled": enabled, "threshold": threshold}
