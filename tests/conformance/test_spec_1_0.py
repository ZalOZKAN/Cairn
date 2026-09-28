"""Conformance checks for `.cairn/` folder format spec 1.0.

These tests exercise spec invariants against a Cairn-produced fixture.
External implementations of the spec can fork this module: replace the
`fresh_cairn_folder` fixture with one that runs your tool's init flow,
and re-run the suite. If all checks pass, you can claim "spec 1.0
round-trip compatible".

The sections named inline below refer to the `.cairn/` folder format specification.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from cairn.memory.memory import Memory


@pytest.fixture
def fresh_cairn_folder() -> Path:
    """Reference writer: produce a populated `.cairn/`-equivalent folder."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        root = Path(td)
        persistence = root / "persistence"
        persistence.mkdir(parents=True)
        db_path = persistence / "memory.db"

        mem = Memory({"memory": {
            "db_path": str(db_path),
            "async_store": False,
            "global_db_path": str(persistence / "global.db"),
        }}, project_dir=root)

        # Seed: one plain memory, one decision with full provenance, one
        # superseded chain so spec readers have something to walk.
        mem.store("plain fact", "the project uses Python 3.10+")
        first = mem.record_decision(
            decision="Use SQLite for local dev",
            reasoning="Zero infra; works on every laptop",
            alternatives=[{"option": "Postgres", "rejected_because": "Overkill"}],
            evidence=[{"source": "team chat", "claim": "agreed in standup"}],
        )
        mem.record_decision(
            decision="Use SQLite + WAL",
            reasoning="WAL lets the dashboard read while the app writes",
            supersedes=first,
        )

        # Spec §6: handoff file with the spec-1.0 headers.
        (persistence / "session-handoff.md").write_text(
            "# Session handoff, 2026-06-14\n\n"
            "## Continuation Point\nWire refresh endpoint to /auth router.\n\n"
            "## In-Progress Tasks\n- JWT refresh-token rotation\n\n"
            "## Completed This Session\n- httpOnly cookie decision recorded\n\n"
            "## Summary\nHalf-day on auth hardening.\n",
            encoding="utf-8",
        )

        # Spec §3: minimal config with format_version.
        (root / "config.yaml").write_text('format_version: "1.0"\n', encoding="utf-8")

        mem.close()
        yield root


# ---- § top-level layout -------------------------------------------------

def test_layout_required_files_exist(fresh_cairn_folder: Path):
    assert (fresh_cairn_folder / "config.yaml").exists()
    assert (fresh_cairn_folder / "persistence" / "memory.db").exists()


# ---- §1 format version --------------------------------------------------

def test_format_version_is_parsable(fresh_cairn_folder: Path):
    import yaml
    cfg = yaml.safe_load((fresh_cairn_folder / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["format_version"] == "1.0"


# ---- §4 memory.db schema ------------------------------------------------

REQUIRED_MEMORIES_COLS = {
    # baseline (v0.4)
    "id", "timestamp", "prompt", "response", "keywords", "importance",
    "content_hash", "valid_from", "valid_until", "invalidated_reason",
    # decision provenance (1.0)
    "kind", "reasoning", "alternatives_json", "superseded_by", "chose_because",
}


def test_memories_table_has_spec_1_0_columns(fresh_cairn_folder: Path):
    db = fresh_cairn_folder / "persistence" / "memory.db"
    with sqlite3.connect(db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
    missing = REQUIRED_MEMORIES_COLS - cols
    assert not missing, f"missing columns: {missing}"


def test_memory_evidence_table_exists(fresh_cairn_folder: Path):
    db = fresh_cairn_folder / "persistence" / "memory.db"
    with sqlite3.connect(db) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(memory_evidence)").fetchall()}
    assert {"id", "memory_id", "source", "claim"} <= cols


def test_active_filter_excludes_invalidated(fresh_cairn_folder: Path):
    """Spec §4: active records are `valid_until IS NULL`."""
    db = fresh_cairn_folder / "persistence" / "memory.db"
    with sqlite3.connect(db) as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE kind='decision'"
        ).fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE kind='decision' AND valid_until IS NULL"
        ).fetchone()[0]
    assert total == 2  # original + supersede
    assert active == 1  # only the head is active


# ---- §4 reader semantics: decision chain --------------------------------

def test_supersedes_chain_walks_forward(fresh_cairn_folder: Path):
    """Following `superseded_by` from any link reaches the active head."""
    db = fresh_cairn_folder / "persistence" / "memory.db"
    with sqlite3.connect(db) as conn:
        head_id = conn.execute(
            "SELECT id FROM memories WHERE kind='decision' AND valid_until IS NULL"
        ).fetchone()[0]
        # Find any non-head row and walk forward.
        non_head_id = conn.execute(
            "SELECT id FROM memories WHERE kind='decision' AND valid_until IS NOT NULL LIMIT 1"
        ).fetchone()[0]
        current = non_head_id
        seen = set()
        while current is not None and current not in seen:
            seen.add(current)
            row = conn.execute(
                "SELECT superseded_by FROM memories WHERE id=?", (current,)
            ).fetchone()
            current = row[0] if row else None
        # The last visited node before exiting the loop must be the head.
    assert head_id in seen


def test_audit_score_derivable_from_raw_columns(fresh_cairn_folder: Path):
    """Spec §4: decision_audit_score = sum(non-empty reasoning, alternatives,
    evidence row count > 0, superseded_by set in either direction)."""
    db = fresh_cairn_folder / "persistence" / "memory.db"
    with sqlite3.connect(db) as conn:
        first_id = conn.execute(
            "SELECT id FROM memories WHERE kind='decision' ORDER BY id LIMIT 1"
        ).fetchone()[0]
        row = conn.execute(
            "SELECT reasoning, alternatives_json, superseded_by FROM memories WHERE id=?",
            (first_id,),
        ).fetchone()
        ev_count = conn.execute(
            "SELECT COUNT(*) FROM memory_evidence WHERE memory_id=?", (first_id,)
        ).fetchone()[0]
        # Also check the inverse direction for chain membership.
        inverse_chain = conn.execute(
            "SELECT id FROM memories WHERE superseded_by=?", (first_id,)
        ).fetchone()
    reasoning, alts_json, super_by = row
    expected_score = sum([
        bool(reasoning),
        bool(alts_json) and alts_json != "[]",
        ev_count > 0,
        super_by is not None or inverse_chain is not None,
    ])
    # The first decision has reasoning + alternatives + evidence + is superseded → 4/4.
    assert expected_score == 4


# ---- §6 session-handoff template ---------------------------------------

REQUIRED_HANDOFF_HEADERS = [
    "## Continuation Point",
    "## In-Progress Tasks",
    "## Completed This Session",
    "## Summary",
]


def test_handoff_headers_match_spec_1_0(fresh_cairn_folder: Path):
    handoff = (fresh_cairn_folder / "persistence" / "session-handoff.md").read_text(encoding="utf-8")
    for header in REQUIRED_HANDOFF_HEADERS:
        assert header in handoff, f"missing required handoff header: {header!r}"
