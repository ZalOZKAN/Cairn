"""Download external benchmark datasets into a local, gitignored cache.

Neither dataset is vendored into the repo:
- LoCoMo (snap-research/locomo) is CC BY-NC 4.0, non-commercial. Cairn is
  MIT/free-forever with no commercial tier, so *using*
  it for benchmarking is unambiguous fair use, but *redistributing* the file
  inside an MIT-licensed repo would be a needless license entanglement.
  Fetched at run time instead, same as every other tool that cites a LoCoMo
  number without shipping the dataset itself.
- LongMemEval (xiaowu0162/longmemeval) is MIT, no such concern, still fetched
  on demand rather than committed, to keep repo size down.

Usage:
    python scripts/download_benchmark_data.py            # both datasets
    python scripts/download_benchmark_data.py --locomo-only
    python scripts/download_benchmark_data.py --longmemeval-only
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / ".bench_cache"

LOCOMO_URL = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"
LOCOMO_FILE = CACHE_DIR / "locomo10.json"

# The "_s" (short) split keeps the run tractable on a laptop.
LONGMEMEVAL_URL = "https://huggingface.co/datasets/xiaowu0162/longmemeval/resolve/main/longmemeval_s"
LONGMEMEVAL_FILE = CACHE_DIR / "longmemeval_s.json"


def _download(url: str, dest: Path, label: str) -> None:
    if dest.exists():
        print(f"[skip] {label} already cached at {dest}")
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[fetch] {label} <- {url}")
    urllib.request.urlretrieve(url, dest)
    print(f"[ok] {label}: {dest.stat().st_size / 1024:.0f} KB")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--locomo-only", action="store_true")
    ap.add_argument("--longmemeval-only", action="store_true")
    args = ap.parse_args()

    if not args.longmemeval_only:
        _download(LOCOMO_URL, LOCOMO_FILE, "LoCoMo (locomo10.json, CC BY-NC 4.0)")
    if not args.locomo_only:
        _download(LONGMEMEVAL_URL, LONGMEMEVAL_FILE, "LongMemEval-S (MIT)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
