"""Decision-provenance walkthrough (Cairn v0.5+ Phase 2 features).

Demonstrates the wedge: recording decisions with reasoning + alternatives +
evidence, superseding a prior decision, walking the chain, scoring audit
completeness, and rendering a single-file HTML dashboard.

Run from the repo root:

    python examples/decision_provenance.py

The script uses a throwaway temp directory so it does not touch your
project's `.cairn/` folder. Open the printed `dashboard.html` path in a
browser at the end to see the dashboard.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Make stdout UTF-8 on Windows code pages that default to cp1252.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from cairn.interface.commands.dashboard import collect_dashboard_data, render_dashboard_html
from cairn.memory.memory import Memory


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        db_path = Path(td) / "memory.db"
        mem = Memory({"memory": {"db_path": str(db_path), "async_store": False}})

        # 1. First decision, fully provenanced.
        first = mem.record_decision(
            decision="Use SQLite for local development storage",
            reasoning="Zero-infra; works on every dev laptop with no setup.",
            alternatives=[
                {"option": "Postgres", "rejected_because": "Overkill for single-dev local workflow"},
                {"option": "DuckDB", "rejected_because": "Newer, smaller ecosystem"},
            ],
            evidence=[
                {"source": "team chat 2026-04-01", "claim": "Two engineers agreed in standup"},
            ],
            chose_because="simplicity",
        )
        print(f"recorded decision #{first}")

        # 2. A second, unrelated decision (will show up separately in the dashboard).
        mem.record_decision(
            decision="Use httpOnly cookies for JWT",
            reasoning="Closes XSS token-exfiltration surface",
            alternatives=[{"option": "localStorage", "rejected_because": "XSS readable"}],
            evidence=[{"source": "OWASP cheatsheet", "claim": "httpOnly blocks JS read"}],
        )

        # 3. Supersede the first decision, the storage choice evolved.
        second = mem.record_decision(
            decision="Use SQLite + WAL for local dev",
            reasoning="WAL mode lets the dashboard read while the app writes",
            supersedes=first,
        )
        print(f"recorded #{second}, superseding #{first}")

        # 4. Walk the chain forward from the original decision.
        chain = mem.thread(first)
        print("\nSupersedes chain:")
        for row in chain:
            marker = "ACTIVE " if row["active"] else "retired"
            print(f"  [{marker}] #{row['id']}  {row['decision']}")

        # 5. Score audit completeness for each decision in the chain.
        print("\nAudit scores:")
        for row in chain:
            score = mem.decision_audit_score(row["id"])
            print(f"  #{row['id']}: {score['score']}/{score['total']}  missing={score['missing']}")

        # 6. Generate a single-file dashboard.
        data = collect_dashboard_data(db_path)
        html_path = Path(td) / "dashboard.html"
        html_path.write_text(render_dashboard_html(data), encoding="utf-8")
        print(f"\nDashboard written: {html_path}")
        print(f"  {data['active_count']} active, {data['invalidated_count']} invalidated decisions")
        print("  Open the file in a browser to see the card grid + modal drill-down.")

        mem.close()


if __name__ == "__main__":
    main()
