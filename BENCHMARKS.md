# Benchmarks

> Status: **v0.5**. All numbers below are Cairn's own, reproducible with
> the commands shown.

## External benchmark, LoCoMo + LongMemEval (evidence-recall@k)


### Why this section exists

This section runs Cairn against two published, independent conversation datasets.

**LoCoMo scoring is under active methodological dispute industry-wide** -
different published numbers use different retrieval protocols (some with
LLM-assisted reranking and extra API calls per query, some without) and
aren't directly comparable despite carrying the same benchmark name. The
protocol below is stated explicitly so Cairn's number doesn't join that
same ambiguity.

### Protocol

- **Metric: evidence-recall@k.** Ingest the surrounding conversation
  turn-by-turn via `Memory.store()`; call `Memory.recall(question, limit=k)`;
  a hit = the evidence turn's own text appears in the recalled content.
  Cairn does no generation, so an LLM-judge-the-final-answer protocol would
  score something Cairn doesn't do, this measures retrieval only.
- **Single retrieval pass. No reranking, no LLM post-processing.**
- **Both backends reported separately**, `sqlite` (FTS5, the documented
  default) and `hybrid` (+ Chroma/`all-MiniLM-L6-v2` embeddings).
- **Datasets, not vendored.** `scripts/download_benchmark_data.py` fetches
  both at run time into a gitignored `.bench_cache/`:
  - **LoCoMo** (`snap-research/locomo`, `locomo10.json`), **CC BY-NC 4.0**.
    Not committed to this MIT repo; fetched on demand.
  - **LongMemEval-S** (`xiaowu0162/longmemeval`), MIT.
- **Runner:** `scripts/benchmark_external.py`.

### Results, 2026-08-09, subset run (see exact counts per row)

| Dataset | Backend | k | evidence-recall@k | items | p50 recall latency |
|---|---|---|---|---|---|
| LoCoMo (2/10 convs) | sqlite | 10 | **54.5%** | 164/301 | 5.6 ms |
| LoCoMo (1/10 conv) | sqlite | 10 | 58.2% | 114/196 | 4.5 ms |
| LoCoMo (1/10 conv) | hybrid | 10 | **64.3%** | 126/196 | 213.7 ms |
| LongMemEval-S (10/500 q) | sqlite | 10 | **90.0%** | 9/10 | 11.4 ms |
| LongMemEval-S (2/500 q) | sqlite | 10 | 50.0% | 1/2 | 12.3 ms |
| LongMemEval-S (2/500 q) | hybrid | 10 | 100.0% | 2/2 | 221.3 ms |

Reproduce:
```bash
python scripts/download_benchmark_data.py
python scripts/benchmark_external.py --locomo-convs 2 --longmemeval-n 10 --k 10
python scripts/benchmark_external.py --locomo-convs 1 --skip-longmemeval --hybrid --k 10
python scripts/benchmark_external.py --skip-locomo --longmemeval-n 2 --hybrid --k 10
```

### Honest reading of these numbers

- **These are small subsets, not the full benchmarks** (LoCoMo: 1-2 of 10
  conversations; LongMemEval-S: 2-10 of 500 questions). The LongMemEval
  hybrid row (n=2) is too small to mean anything on its own, it is
  reported anyway because incomplete data still gets published as-is here,
  not because 1/2 vs 2/2 is a meaningful sample. A fuller run
  (`--locomo-convs 10 --longmemeval-n 100`+) is future work; hybrid backend
  runtime (~280ms/store, Chroma+ONNX embeddings) makes a full hybrid run
  take on the order of tens of minutes.
- **`hybrid` beats `sqlite` on recall** (+6pp LoCoMo, +50pp LongMemEval on a
  tiny sample) **at a real latency cost** (~40-50x slower recall,
  order-of-magnitude slower writes). Cairn's documented default is
  `sqlite`/FTS5, a headline "Cairn: X% recall" number without the backend
  label would be misleading either direction.
- **LoCoMo scores meaningfully lower than LongMemEval-S in this run**
  (54-64% vs 50-100%, small-n LongMemEval caveat aside). Plausible cause:
  LoCoMo's conversations are longer and denser (400+ turns per
  conversation, many single-hop factual QA competing for the same top-k
  slots) than a given LongMemEval-S haystack. Not yet root-caused; flagged
  here rather than smoothed over.
- **Do not cite a single number as "Cairn's LoCoMo score."** Cite dataset +
  backend + k + subset size together, exactly as the table does.
