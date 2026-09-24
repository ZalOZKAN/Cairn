from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from cairn.memory.memory import Memory


def _mem(root: Path) -> Memory:
    return Memory({"memory": {"async_store": False, "global_enabled": False}}, project_dir=root)


def test_fts_path_runs_and_stems():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        root = Path(d)
        (root / ".cairn").mkdir()
        m = _mem(root)
        assert m.fts5_enabled
        m.record_decision("Deploy on Fly.io", reasoning="regional failover")
        rows = m._sqlite_keyword_search("where is it deployed")
        assert rows and rows[0]["prompt"] == "Deploy on Fly.io"


def test_pre_porter_index_is_rebuilt_with_existing_rows():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        root = Path(d)
        (root / ".cairn").mkdir()
        m = _mem(root)
        m.record_decision("Render invoices with WeasyPrint", reasoning="designers edit html")
        m.close()
        with sqlite3.connect(m.db_path) as conn:
            for trg in ("trg_memories_ai", "trg_memories_ad", "trg_memories_au"):
                conn.execute(f"DROP TRIGGER {trg}")
            conn.execute("DROP TABLE memories_fts")
            conn.execute(
                "CREATE VIRTUAL TABLE memories_fts USING fts5(prompt, response, "
                "content=memories, content_rowid=id)"
            )
        m2 = _mem(root)
        with sqlite3.connect(m2.db_path) as conn:
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='memories_fts'").fetchone()[0]
        assert "porter" in sql
        assert m2._sqlite_keyword_search("rendering invoice")
