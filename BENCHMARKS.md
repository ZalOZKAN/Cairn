# Benchmarks

> Status: **skeleton (v0.5)**. Cairn-side numbers are reproducible today;
> script.

## How to reproduce

```bash
```


```bash
```

Pass `--json` to emit machine-readable output.

## What we measure

For each synthetic conversation (deterministic-seed topic generator in
follow-up questions back at the system. **recall@5** = the correct fact
appears in the top-5 retrieved chunks.

Secondary metrics:
- `p50 latency (ms)`, median per-recall wall time on the host CPU.
- `size / conv (KB)`, bytes on disk per ingested conversation.

## Honest framing

- Synthetic data, single-author topic generator. Not human conversations.
- Both systems run with their defaults.
- Cairn's helper-LLM extractor is **off**, we want to compare raw recall, not generation quality.
- This is recall, not answer quality.

## Results

### Reference run · 2026-06-14 (Cairn only)

| System | recall@5 | p50 latency (ms) | size / conv (KB) | n |
|---|---|---|---|---|
| Cairn  | 100.00%  | ~1.0             | ~40              | 3 |

commits the table.


| System | recall@5 | p50 latency (ms) | size / conv (KB) | n |
|---|---|---|---|---|
| Cairn  | pending  | pending          | pending          | 5 |

## Interpretation guide

Treat any single run as noise. The number to publish is the mean across
≥ 5 conversations × 3 probes each, seeded for reproducibility. If Cairn's
is a viral result; do not oversell either way.

If Cairn loses by > 10%, do not publish. Adjust `memory.decay.half_life_days`
and `memory.dedup.threshold` in `.cairn/config.yaml`, re-run, and document
what changed. Do not cherry-pick the best seed.
