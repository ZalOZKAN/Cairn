"""External benchmark runner, LoCoMo + LongMemEval, evidence-recall@k.

Answers the open question: how does Cairn's recall compare to a
third-party benchmark, not just its own internal decay ablation
(`benchmark_decay.py`)?

# Protocol (documented here so the published number is falsifiable)

- **Metric: evidence-recall@k.** For each QA item, ingest the surrounding
  conversation turn-by-turn via `Memory.store()`, then call
  `Memory.recall(question, limit=k)`. A hit = the evidence turn's own text
  appears in the recalled content. This mirrors the metric pmb publishes
  ("evidence-recall@10"), not "did an LLM judge the final answer correct" -
  Cairn does no generation, so an LLM-judge protocol would be measuring
  something Cairn doesn't do.
- **Single retrieval pass. No reranking, no LLM post-processing.** LoCoMo
  scores are currently disputed industry-wide precisely because projects
  don't agree on this point, publishing the protocol next to the number is
  the whole point of this script.
- **Both backends reported separately** (`sqlite` = FTS5 default, `hybrid` =
  + Chroma/embeddings), they differ substantially and a bare "Cairn: X%"
  number is meaningless without saying which one.
- **Subset, not full benchmark**, for laptop runtime, see `--help` for size
  and the exact counts printed in the output; `BENCHMARKS.md` records
  whatever was actually run.

# Setup

    python scripts/download_benchmark_data.py
    python scripts/benchmark_external.py                    # sqlite, small subset
    python scripts/benchmark_external.py --hybrid            # + hybrid backend (slow: ~0.3s/store)
    python scripts/benchmark_external.py --locomo-convs 5 --longmemeval-n 30
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CACHE_DIR = Path(__file__).resolve().parent.parent / ".bench_cache"
LOCOMO_FILE = CACHE_DIR / "locomo10.json"
LONGMEMEVAL_FILE = CACHE_DIR / "longmemeval_s.json"


def _mk_memory(td: str, backend: str) -> Any:
    from cairn.memory.memory import Memory
    return Memory({"memory": {
        "db_path": str(Path(td) / "mem.db"),
        "global_db_path": str(Path(td) / "global.db"),
        "backend": backend,
        "async_store": False,
        "dedup": {"enabled": False},
        "decay": {"enabled": True},
    }})


# --------------------------------------------------------------- LoCoMo --

def run_locomo(backend: str, n_convs: int, k: int, data: list[dict] | None = None) -> dict[str, Any]:
    if data is None:
        data = json.loads(LOCOMO_FILE.read_text(encoding="utf-8"))
    data = data[:n_convs]
    hits = 0
    total = 0
    latencies: list[float] = []
    store_times: list[float] = []

    for conv in data:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            mem = _mk_memory(td, backend)
            turn_by_id: dict[str, str] = {}
            t0 = time.perf_counter()
            for key, sessions in conv["conversation"].items():
                if not key.startswith("session_") or key.endswith("date_time"):
                    continue
                for turn in sessions:
                    text = f"{turn['speaker']}: {turn['text']}"
                    mem.store(prompt=text, response=turn["text"])
                    turn_by_id[turn["dia_id"]] = turn["text"]
            store_times.append(time.perf_counter() - t0)

            for qa in conv["qa"]:
                evidence_ids = qa.get("evidence") or []
                evidence_texts = [turn_by_id[e] for e in evidence_ids if e in turn_by_id]
                if not evidence_texts:
                    continue
                total += 1
                t0 = time.perf_counter()
                out = mem.recall(qa["question"], limit=k) or ""
                latencies.append((time.perf_counter() - t0) * 1000)
                if any(ev[:40] in out for ev in evidence_texts):
                    hits += 1
            mem.close()

    return {
        "dataset": "LoCoMo", "backend": backend, "k": k,
        "convs": len(data), "items": total, "hits": hits,
        "evidence_recall": hits / total if total else 0.0,
        "p50_latency_ms": statistics.median(latencies) if latencies else None,
        "p50_ingest_s_per_conv": statistics.median(store_times) if store_times else None,
    }


# ---------------------------------------------------------- LongMemEval --

def run_longmemeval(backend: str, n_questions: int, k: int, data: list[dict] | None = None) -> dict[str, Any]:
    if data is None:
        data = json.loads(LONGMEMEVAL_FILE.read_text(encoding="utf-8"))
    data = data[:n_questions]
    hits = 0
    total = 0
    latencies: list[float] = []
    store_times: list[float] = []

    for q in data:
        answer_ids = set(q.get("answer_session_ids") or [])
        if not answer_ids:
            continue
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            mem = _mk_memory(td, backend)
            answer_texts: list[str] = []
            t0 = time.perf_counter()
            for sid, session in zip(q["haystack_session_ids"], q["haystack_sessions"]):
                for turn in session:
                    content = turn.get("content", "")
                    role = turn.get("role", "user")
                    mem.store(prompt=f"{role}: {content}"[:2000], response=content[:2000])
                    if sid in answer_ids:
                        answer_texts.append(content)
            store_times.append(time.perf_counter() - t0)

            if not answer_texts:
                mem.close()
                continue
            total += 1
            t0 = time.perf_counter()
            out = mem.recall(q["question"], limit=k) or ""
            latencies.append((time.perf_counter() - t0) * 1000)
            if any(a[:40] in out for a in answer_texts):
                hits += 1
            mem.close()

    return {
        "dataset": "LongMemEval-S", "backend": backend, "k": k,
        "questions": len(data), "items": total, "hits": hits,
        "evidence_recall": hits / total if total else 0.0,
        "p50_latency_ms": statistics.median(latencies) if latencies else None,
        "p50_ingest_s_per_haystack": statistics.median(store_times) if store_times else None,
    }


# --------------------------------------------------------------- main --

def fmt_result(r: dict[str, Any]) -> str:
    n = r.get("convs") or r.get("questions")
    return (
        f"{r['dataset']} / {r['backend']} / k={r['k']}: "
        f"evidence-recall@{r['k']} = {r['evidence_recall']:.1%} "
        f"({r['hits']}/{r['items']} items, {n} conversations/questions) "
        f"- p50 recall latency {r['p50_latency_ms']:.1f}ms" if r.get("p50_latency_ms") else
        f"{r['dataset']} / {r['backend']}: no scoreable items in this subset"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--locomo-convs", type=int, default=2, help="of 10 available")
    ap.add_argument("--longmemeval-n", type=int, default=10, help="of 500 available")
    ap.add_argument("--hybrid", action="store_true", help="also run backend=hybrid (slow: Chroma embeddings)")
    ap.add_argument("--skip-locomo", action="store_true")
    ap.add_argument("--skip-longmemeval", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not args.skip_locomo and not LOCOMO_FILE.exists():
        print(f"[error] {LOCOMO_FILE} missing, run scripts/download_benchmark_data.py first", file=sys.stderr)
        return 1
    if not args.skip_longmemeval and not LONGMEMEVAL_FILE.exists():
        print(f"[error] {LONGMEMEVAL_FILE} missing, run scripts/download_benchmark_data.py first", file=sys.stderr)
        return 1

    backends = ["sqlite"] + (["hybrid"] if args.hybrid else [])
    results: list[dict[str, Any]] = []

    for backend in backends:
        if not args.skip_locomo:
            results.append(run_locomo(backend, args.locomo_convs, args.k))
        if not args.skip_longmemeval:
            results.append(run_longmemeval(backend, args.longmemeval_n, args.k))

    if args.json:
        json.dump(results, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"\nCairn external benchmark, evidence-recall@{args.k}, single retrieval pass, no reranking\n")
        for r in results:
            print(fmt_result(r))
        print(
            "\nSubset sizes: LoCoMo "
            f"{args.locomo_convs}/10 conversations, LongMemEval-S {args.longmemeval_n}/500 questions "
            "(each with its own haystack). See BENCHMARKS.md for the full methodology note."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
