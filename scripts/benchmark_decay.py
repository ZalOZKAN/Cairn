"""
Memory decay benchmark, recall@5 with decay ON vs OFF.

Setup:
- Seed 15 scenarios: each has a "stale" answer (older, lower importance)
  and a "fresh" answer (newer, higher importance) for the same topic.
- For each topic, query and check whether the FRESH answer appears in top-5.
- recall@5 = (queries where fresh is in top-5) / total queries.

The hypothesis: decay-ON should beat decay-OFF, because decay-OFF ranks
purely by raw importance × keyword overlap, while decay-ON penalizes
older entries even when their importance is equal or slightly higher.

Run:
    python scripts/benchmark_decay.py
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Local import path setup (run from repo root, no install needed)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cairn.config.loader import load_config  # noqa: E402
from cairn.memory.memory import Memory  # noqa: E402


# Harder setup: stale entries have HIGHER raw importance than fresh.
# This simulates the real failure mode: "I marked the old decision as important
# when I made it. Now there's a newer decision that should win." Without decay,
# raw importance ranks stale first.
# Fresh entries are also buried under noise so they fall OUT of the
# sliding_window, recency_boost can no longer save them.
# (topic_query, stale_response, fresh_response, stale_age_days, fresh_age_days,
#  stale_importance, fresh_importance)
SCENARIOS: list[tuple[str, str, str, int, int, int, int]] = [
    ("postgres database choice", "Use MySQL for our service", "Switched to Postgres after JSON column needs", 200, 30, 5, 2),
    ("jwt auth approach", "Store JWT in localStorage", "Use httpOnly cookie for JWT to avoid XSS", 180, 25, 5, 2),
    ("logging library python", "Use print statements for logs", "Adopted structlog for structured JSON logs", 240, 40, 4, 2),
    ("api versioning strategy", "Header-based API versioning", "URL-path versioning v1/v2 chosen", 220, 35, 5, 2),
    ("frontend framework", "Settled on Vue for dashboard", "Migrated dashboard to React + tanstack", 260, 45, 5, 2),
    ("cache layer redis", "Use in-process LRU cache", "Added Redis for shared cache across pods", 190, 20, 4, 2),
    ("test runner choice", "Using unittest stdlib", "Switched test runner to pytest", 300, 50, 4, 2),
    ("password hashing scheme", "bcrypt with cost 10", "Argon2id is now the standard for passwords", 360, 60, 5, 3),
    ("background job queue", "Run jobs with apscheduler", "Replaced apscheduler with rq + Redis", 210, 30, 5, 2),
    ("deployment target cloud", "Deploy to Heroku", "Deploying to Fly.io for cheaper egress", 240, 35, 5, 2),
    ("container orchestration", "Docker Compose on a VM", "Kubernetes via k3s on Hetzner", 280, 45, 5, 2),
    ("python version target", "Targeting Python 3.10", "Bumped target Python to 3.12", 180, 30, 4, 2),
    ("packaging tool", "setup.py + setuptools", "Adopted hatch as build backend", 230, 40, 4, 2),
    ("linter setup", "Using flake8 + isort", "Switched to ruff (single binary)", 200, 25, 4, 2),
    ("env management", "venv per project", "Standardized on uv for venv + pip", 170, 20, 4, 2),
]


NOISE_BEFORE = 30   # buries stale rows
NOISE_AFTER = 40    # buries fresh rows past sliding_window=10


def _insert_aged(mem: Memory, prompt: str, response: str, days_old: int, importance: int) -> int:
    """Insert directly with a synthetic past timestamp (bypasses async writer)."""
    ts = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    entry = {
        "timestamp": ts,
        "prompt": prompt,
        "response": response,
        "keywords": Memory._extract_keywords(prompt + " " + response[:200]),
        "importance": importance,
        "content_hash": Memory._content_hash(prompt, response),
    }
    with sqlite3.connect(mem.db_path) as conn:
        cur = conn.execute(
            "INSERT INTO memories (timestamp, prompt, response, keywords, importance, "
            "content_hash, valid_from) VALUES (?,?,?,?,?,?,?)",
            (ts, prompt, response[:3000], entry["keywords"], importance,
             entry["content_hash"], ts),
        )
        conn.commit()
        return cur.lastrowid


def _run(decay_enabled: bool) -> tuple[float, list[tuple[str, bool]]]:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        cfg = load_config()
        cfg["memory"]["db_path"] = f"{td}/bench.db"
        cfg["memory"]["global_db_path"] = f"{td}/global.db"
        cfg["memory"]["async_store"] = False
        cfg["memory"]["dedup"] = {"enabled": False}
        cfg["memory"]["decay"] = {"enabled": decay_enabled, "half_life_days": 30}
        mem = Memory(cfg)

        # 1) Insert stale rows
        for q, stale, fresh, sage, fage, simp, fimp in SCENARIOS:
            _insert_aged(mem, q, stale, sage, simp)
        # 2) Noise before fresh
        for i in range(NOISE_BEFORE):
            _insert_aged(mem, f"unrelated topic {i} alpha bravo", f"noise body {i}", 60, 1)
        # 3) Fresh rows
        for q, stale, fresh, sage, fage, simp, fimp in SCENARIOS:
            _insert_aged(mem, q, fresh, fage, fimp)
        # 4) Heavy noise AFTER fresh, pushes fresh out of sliding_window=10
        for i in range(NOISE_AFTER):
            _insert_aged(mem, f"later filler topic {i} delta echo", f"filler body {i}", 1, 1)

        # Query each topic; check whether FRESH response wins top-1 in recall output
        results = []
        hits = 0
        for q, stale, fresh, *_ in SCENARIOS:
            recalled = mem.recall(q, limit=5)
            fresh_seen = fresh[:30] in recalled
            stale_seen = stale[:30] in recalled
            # "fresh wins" if fresh is present AND ranked before stale (or stale absent)
            if fresh_seen and (not stale_seen or recalled.index(fresh[:30]) < recalled.index(stale[:30])):
                hits += 1
                results.append((q, True))
            else:
                results.append((q, False))
        return hits / len(SCENARIOS), results


def main() -> int:
    print("=" * 70)
    print("Cairn -- Memory Decay Benchmark")
    print(f"Scenarios: {len(SCENARIOS)} (each has stale vs fresh on same topic)")
    print("Metric: recall@5 -- fresh answer must rank above stale in top-5")
    print("=" * 70)

    off_score, off_detail = _run(decay_enabled=False)
    on_score, on_detail = _run(decay_enabled=True)

    print(f"\nDecay OFF -> recall@5 = {off_score:.2%}")
    print(f"Decay ON  -> recall@5 = {on_score:.2%}")
    delta = (on_score - off_score) * 100
    print(f"Delta     -> {delta:+.1f} pp\n")

    print("Per-topic outcome (Y = fresh wins, N = fresh missed/lost):")
    print(f"{'topic':<40}  {'OFF':>4}  {'ON':>4}")
    for (q, off_ok), (_, on_ok) in zip(off_detail, on_detail):
        print(f"{q:<40}  {'Y' if off_ok else 'N':>4}  {'Y' if on_ok else 'N':>4}")

    return 0 if on_score >= off_score else 1


if __name__ == "__main__":
    raise SystemExit(main())
