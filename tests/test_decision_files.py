from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from cairn.memory import decision_files
from cairn.memory.memory import Memory


def _mem(root: Path) -> Memory:
    (root / ".cairn").mkdir(exist_ok=True)
    return Memory({"memory": {"async_store": False, "global_enabled": False}}, project_dir=root)


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        yield Path(d)


def _files(root: Path) -> list[Path]:
    return sorted((root / ".cairn" / "decisions").glob("*.md"))


def test_render_parse_round_trip():
    d = {
        "uid": "20260924-abc123", "date": "2026-09-24T10:00:00+00:00", "status": "active",
        "importance": 3, "path": "apps/api", "decision": "Use httpOnly cookies for JWT",
        "reasoning": "Closes the XSS token exfiltration vector.",
        "chose_because": "security over convenience",
        "alternatives": [{"option": "localStorage", "rejected_because": "readable by any script"}],
        "evidence": [{"source": "OWASP", "claim": "tokens in localStorage are XSS-exposed"}],
    }
    text = decision_files.render(d)
    parsed = decision_files.parse(text)
    assert decision_files.render(parsed) == text
    assert parsed["date"] == d["date"]
    assert parsed["alternatives"] == d["alternatives"]


def test_parse_rejects_files_without_uid():
    with pytest.raises(ValueError):
        decision_files.parse("---\nstatus: active\n---\n# x\n")


def test_record_writes_one_file_per_decision(root):
    m = _mem(root)
    m.record_decision(
        "Use postgres for billing", reasoning="acid",
        alternatives=[{"option": "mongodb", "rejected_because": "no transactions we trust"}],
        evidence=[{"source": "load test", "claim": "p99 fine"}],
    )
    m.record_decision("Adopt ruff")
    files = _files(root)
    assert len(files) == 2
    text = next(f for f in files if "postgres" in f.name).read_text(encoding="utf-8")
    assert "## Alternatives" in text and "**mongodb**" in text and "## Evidence" in text


def test_supersede_updates_both_files(root):
    m = _mem(root)
    old = m.record_decision("Store JWT in localStorage")
    m.record_decision("Store JWT in httpOnly cookie", supersedes=old)
    old_file = next(f for f in _files(root) if "localstorage" in f.name)
    new_file = next(f for f in _files(root) if "httponly" in f.name)
    old_d = decision_files.parse(old_file.read_text(encoding="utf-8"))
    new_d = decision_files.parse(new_file.read_text(encoding="utf-8"))
    assert old_d["status"] == "superseded"
    assert old_d["superseded_by"] == new_d["uid"]
    assert new_d["supersedes"] == old_d["uid"]


def test_invalidate_updates_file(root):
    m = _mem(root)
    mid = m.record_decision("Use celery for jobs")
    m.invalidate(mid, reason="moved to cron")
    d = decision_files.parse(_files(root)[0].read_text(encoding="utf-8"))
    assert d["status"] == "invalidated" and d["invalidated_reason"] == "moved to cron"


def test_teammate_file_is_imported_and_recallable(root):
    m = _mem(root)
    m.record_decision("Placeholder so the folder exists")
    teammate = decision_files.render({
        "uid": "20260901-feed01", "date": "2026-09-01T09:00:00+00:00", "status": "active",
        "importance": 3, "decision": "Deploy the API on fly.io",
        "reasoning": "cheapest option with regional failover",
    })
    (root / ".cairn" / "decisions" / "20260901-feed01-deploy-the-api-on-fly-io.md").write_text(teammate, encoding="utf-8")
    counts = _mem(root).sync_decision_files()
    assert counts["imported"] == 1
    assert "fly.io" in _mem(root).recall("deploy api fly.io regional")


def test_hand_edit_wins_over_db(root):
    m = _mem(root)
    mid = m.record_decision("Use redis for cache", reasoning="fast")
    f = _files(root)[0]
    f.write_text(f.read_text(encoding="utf-8").replace("fast", "fast and already in our stack"), encoding="utf-8")
    counts = m.sync_decision_files()
    assert counts["updated"] == 1
    assert m.get_full(mid)["reasoning"] == "fast and already in our stack"
    assert m.sync_decision_files()["updated"] == 0


def test_supersede_written_only_in_files_is_applied(root):
    m = _mem(root)
    old = m.record_decision("Use REST for the public API")
    old_uid = decision_files.parse(_files(root)[0].read_text(encoding="utf-8"))["uid"]
    newer = decision_files.render({
        "uid": "20260925-beef02", "date": "2026-09-25T09:00:00+00:00", "status": "active",
        "importance": 2, "supersedes": old_uid, "decision": "Use GraphQL for the public API",
    })
    (root / ".cairn" / "decisions" / "20260925-beef02-graphql.md").write_text(newer, encoding="utf-8")
    m.sync_decision_files()
    assert m.get_full(old)["valid_until"] is not None
    assert m.get_full(old)["superseded_by"] is not None


def test_reindex_rebuilds_from_files(root):
    m = _mem(root)
    m.record_decision("Use pnpm workspaces", reasoning="monorepo")
    with sqlite3.connect(m.db_path) as conn:
        conn.execute("UPDATE memories SET prompt='corrupted' WHERE kind='decision'")
    counts = m.reindex_decisions()
    assert counts["imported"] == 1
    assert "pnpm" in m.recall("pnpm workspaces monorepo")


def test_broken_file_is_skipped_not_fatal(root, capsys):
    m = _mem(root)
    m.record_decision("Use black formatting")
    (root / ".cairn" / "decisions" / "broken.md").write_text("no front matter", encoding="utf-8")
    counts = m.sync_decision_files()
    assert counts["errors"] == 1
    assert "broken.md" in capsys.readouterr().err


def test_legacy_rows_without_uid_get_exported(root):
    m = _mem(root)
    m.record_decision("Legacy decision about logging")
    for f in _files(root):
        f.unlink()
    with sqlite3.connect(m.db_path) as conn:
        conn.execute("UPDATE memories SET uid=NULL")
    counts = m.sync_decision_files()
    assert counts["exported"] == 1 and len(_files(root)) == 1


NYGARD = """# 3. Use PostgreSQL for persistence

Date: 2025-11-02

## Status

Accepted

## Context

We need transactions across orders and payments.

## Decision

We will use PostgreSQL 16.

## Consequences

Ops must run backups.
"""

MADR = """# Choose a message broker

* Status: proposed

## Considered Options

* Kafka
* RabbitMQ
"""


def test_import_adrs_is_idempotent_and_skips_proposed(root):
    adr = root / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0003-use-postgresql.md").write_text(NYGARD, encoding="utf-8")
    (adr / "0004-broker.md").write_text(MADR, encoding="utf-8")
    m = _mem(root)
    assert m.import_adrs(adr) == {"imported": 1, "skipped": 1}
    assert m.import_adrs(adr) == {"imported": 0, "skipped": 2}
    d = decision_files.parse(_files(root)[0].read_text(encoding="utf-8"))
    assert d["uid"].startswith("20251102-")
    assert "PostgreSQL 16" in d["decision"] and "transactions" in d["reasoning"]
    assert "postgresql" in m.recall("postgresql persistence transactions").lower()
