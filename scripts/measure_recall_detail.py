"""How many characters does a recall cost in full mode versus index mode plus `memory_get`?

Seeds the decision pool of `eval_decisions.py`, asks each paraphrased question with limit 5, and counts the characters
the agent would read: full recall, or the index plus the first N entries opened with `get_full` (printed exactly as the
MCP `memory_get` handler prints them). Characters, not tokens: the project does not estimate tokens.

Usage:
    python scripts/measure_recall_detail.py
    python scripts/measure_recall_detail.py --json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from eval_decisions import DECISIONS  # noqa: E402

from cairn.mcp.server import record_json  # noqa: E402
from cairn.memory.memory import Memory  # noqa: E402

LIMIT = 5
OPENED = (0, 1, 2, 3)


FILLER = " The team weighed this against the alternatives, tried it on a branch and kept it after review."


def run(reasoning_chars: int = 0) -> dict:
    """`reasoning_chars`: pad each decision's reasoning to about this length, to model a store with long entries."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        root = Path(d)
        (root / ".cairn").mkdir()
        m = Memory({"memory": {"async_store": False, "global_enabled": False}}, project_dir=root)
        ids = [
            m.record_decision(
                decision, reasoning=(reasoning + FILLER * (reasoning_chars // len(FILLER) + 1))[:reasoning_chars]
                if reasoning_chars else reasoning,
                alternatives=[{"option": rejected, "rejected_because": reasoning}], scope_path=path,
            )
            for decision, reasoning, rejected, path, _, _ in DECISIONS
        ]
        full_chars = 0
        index_chars = {n: 0 for n in OPENED}
        index_hits = full_hits = 0
        for mid, (_, _, _, _, _, question) in zip(ids, DECISIONS):
            full = m.recall(question, limit=LIMIT)
            index = m.recall(question, limit=LIMIT, detail="index")
            full_chars += len(full)
            full_hits += f"[#{mid} " in full
            index_hits += f"[#{mid} " in index
            shown = [int(i) for i in re.findall(r"^\[#(\d+) ", index, flags=re.M)]
            for n in OPENED:
                opened = sum(
                    len(record_json(m.get_full(i))) for i in shown[:n]
                )
                index_chars[n] += len(index) + opened
        m.close()
        total = len(DECISIONS)
        return {
            "questions": total,
            "full_chars": full_chars,
            "index_then_open": {
                str(n): {"chars": c, "change_vs_full": round(c / full_chars - 1, 3)} for n, c in index_chars.items()
            },
            "hit_in_full": round(full_hits / total, 3),
            "hit_in_index": round(index_hits / total, 3),
        }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--reasoning-chars", type=int, default=0, help="pad each reasoning to about this length")
    args = ap.parse_args()
    res = run(args.reasoning_chars)
    if args.json:
        print(json.dumps(res, indent=2))
        return
    print(f"questions: {res['questions']}   full recall: {res['full_chars']} characters")
    for n, row in res["index_then_open"].items():
        print(f"index + open {n}: {row['chars']} characters ({row['change_vs_full']:+.0%} vs full)")
    print(f"expected decision among the listed entries: full {res['hit_in_full']:.0%}, index {res['hit_in_index']:.0%}")


if __name__ == "__main__":
    main()
