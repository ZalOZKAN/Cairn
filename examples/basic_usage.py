"""
Cairn, basic usage examples.

Run:
  pip install cairn-memory
  python examples/basic_usage.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from cairn import Memory, Project


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db_path = str(Path(tmp) / "memory.db")

        print("=" * 50)
        print("1. Store + recall through the Memory layer")
        print("=" * 50)
        memory = Memory({"memory": {
            "db_path": db_path,
            "async_store": False,
            "global_db_path": str(Path(tmp) / "global.db"),
        }})
        memory.store(
            "JWT storage decision",
            "Use httpOnly cookies for JWT, not localStorage. XSS exfiltration risk.",
            importance=2,
        )
        memory.store(
            "Database for local dev",
            "Use SQLite for local development. Postgres only in staging+.",
            importance=2,
        )
        recall = memory.recall("jwt storage")
        print(f"  Recalled: {recall[:200]}...\n" if len(recall) > 200 else f"  Recalled: {recall}\n")

        print("=" * 50)
        print("2. Invalidate a superseded decision")
        print("=" * 50)
        ok = memory.invalidate(1, reason="schema migrated to Postgres for all envs")
        print(f"  invalidate(1) -> {ok}")
        recall_again = memory.recall("jwt storage")
        print(f"  Recall after invalidate: {recall_again[:200]}\n")

        print("=" * 50)
        print("3. check_rejected, has this approach already been tried?")
        print("=" * 50)
        rejects = memory.check_rejected("store the auth token in localStorage")
        if rejects:
            print(f"  Found {len(rejects)} prior rejection(s): {rejects[0]['reason']}")
        else:
            print("  No record of this being tried before.")

        print("=" * 50)
        print("4. Project facade, config + memory in one object")
        print("=" * 50)
        project = Project(load_session=False)
        print(f"  memory_helper_model: {project.config.get('memory_helper_model')}")
        print("  project.memory is the same Memory class used above")


if __name__ == "__main__":
    main()
