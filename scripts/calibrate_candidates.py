"""
Threshold calibration for the duplicate / related / conflict heuristics.

Scores hand-labeled decision pairs against the real dedup.candidate_relation
and dedup.conflict_relation, sweeping their thresholds. Thresholds are picked
on PAIRS and then checked on HELD_OUT, pairs never used for picking, so an
overfit choice shows up as a drop there. The pairs are written by hand, so
this shows how the heuristic behaves on realistic phrasings; it does not
replace tuning on your own decision history. Add pairs from real projects.

Labels: dup (same decision reworded), related (same topic, distinct),
diff (differs in a negation, number or version: must NOT be called a
duplicate), none (unrelated).

Run:
    python scripts/calibrate_candidates.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cairn.memory import dedup  # noqa: E402

PAIRS: list[tuple[str, str, str]] = [
    ("dup", "Use SQLite for local storage", "Use SQLite for the local storage layer"),
    ("dup", "Store sessions in Redis with a one hour expiry", "Sessions are stored in Redis, expiry of one hour"),
    ("dup", "Run ruff in CI before the test job", "In CI run ruff before the tests job"),
    ("dup", "Keep decision files in the decisions directory as markdown",
     "Decision files live as markdown in the decisions directory"),
    ("dup", "Use httpOnly cookies for the JWT refresh token", "The JWT refresh token goes in httpOnly cookies"),
    ("dup", "Log structured JSON from the CLI", "The CLI logs structured JSON"),
    ("dup", "Pin ruff to an exact version in dev dependencies", "Dev dependencies pin ruff to an exact version"),
    ("dup", "Cache embeddings on disk under the project cache directory",
     "Embeddings are cached on disk in the project cache directory"),
    ("diff", "Use Redis for the cache", "Do not use Redis for the cache"),
    ("diff", "Retry failed requests 3 times", "Retry failed requests 5 times"),
    ("diff", "Support Python 3.10 and newer", "Support Python 3.12 and newer"),
    ("diff", "Always run the migration before the tests", "Never run the migration before the tests"),
    ("diff", "Use the v2 API for the importer", "Use the v3 API for the importer"),
    ("diff", "Enable the cache for the build step", "Avoid the cache for the build step"),
    ("diff", "Keep 10 days of logs on disk", "Keep 30 days of logs on disk"),
    ("diff", "Run the linter with the strict config", "Run the linter without the strict config"),
    ("related", "Use SQLite for local storage", "Use SQLite backups before every schema migration"),
    ("related", "Use httpOnly cookies for the JWT refresh token", "Rotate the JWT refresh token on every login"),
    ("related", "Run ruff in CI before the test job", "Run the decay benchmark in CI after the test job"),
    ("related", "Log structured JSON from the CLI", "Print human readable tables from the CLI by default"),
    ("related", "Keep decision files in the decisions directory", "Decision files carry a uid in their front matter"),
    ("related", "Cache embeddings on disk", "Embeddings use the small local model by default"),
    ("none", "Use SQLite for local storage", "Name the release branch after the version"),
    ("none", "Run ruff in CI before the test job", "Write commit messages in the imperative mood"),
    ("none", "Log structured JSON from the CLI", "Pin the docker base image by digest"),
    ("none", "Use httpOnly cookies for the JWT refresh token", "Benchmarks use a fixed random seed"),
    ("none", "Keep decision files in markdown", "Windows builds run on the latest runner image"),
    ("none", "Cache embeddings on disk", "Issue templates live in the github directory"),
]

HELD_OUT: list[tuple[str, str, str]] = [
    ("dup", "Write decision ids as g plus a number for global entries",
     "Global entries get ids written as g plus a number"),
    ("dup", "Measure tokens as characters divided by four", "Tokens are measured as characters divided by four"),
    ("dup", "Ignore the bench cache directory in git", "The bench cache directory is ignored in git"),
    ("dup", "Keep the MCP tool descriptions short", "MCP tool descriptions are kept short"),
    ("dup", "Release builds run from tagged commits only", "Only tagged commits trigger release builds"),
    ("diff", "Allow 2 retries for the helper model", "Allow 4 retries for the helper model"),
    ("diff", "Ship the vector extra by default", "Do not ship the vector extra by default"),
    ("diff", "Target Python 3.10 in ruff", "Target Python 3.11 in ruff"),
    ("related", "Ignore the bench cache directory in git", "Download the bench datasets on demand"),
    ("related", "Keep the MCP tool descriptions short", "Offer a core MCP tool profile for new projects"),
    ("related", "Measure tokens as characters divided by four", "Cap the boot context with a token budget"),
    ("none", "Release builds run from tagged commits only", "Decision files carry a uid"),
    ("none", "Keep the MCP tool descriptions short", "Use SQLite for local storage"),
    ("none", "Allow 2 retries for the helper model", "Name chapters with short noun phrases"),
]

# Same labels, other languages: reported apart so a language that lags shows.
MULTILINGUAL: list[tuple[str, str, str, str]] = [
    ("tr", "dup", "Yerel depolama için SQLite kullan", "Yerel depolama katmanı için SQLite kullan"),
    ("tr", "dup", "CI içinde testlerden önce ruff çalıştır", "Testlerden önce CI içinde ruff çalıştır"),
    ("tr", "diff", "Önbellek için Redis kullan", "Önbellek için Redis kullanma"),
    ("tr", "diff", "Başarısız istekleri 3 kez tekrarla", "Başarısız istekleri 5 kez tekrarla"),
    ("tr", "diff", "Oturumlar için çerez kullanalım", "Oturumlar için çerez kullanmayalım"),
    ("tr", "none", "Yerel depolama için SQLite kullan", "Sürüm dalını sürüm numarasıyla adlandır"),
    ("fr", "dup", "Utiliser SQLite pour le stockage local", "Utiliser SQLite pour la couche de stockage local"),
    ("fr", "diff", "Utiliser Redis pour le cache", "Ne pas utiliser Redis pour le cache"),
    ("fr", "none", "Utiliser SQLite pour le stockage local", "Nommer la branche de version"),
    ("es", "dup", "Usar SQLite para el almacenamiento local", "Usar SQLite para la capa de almacenamiento local"),
    ("es", "diff", "Usar Redis para la caché", "Nunca usar Redis para la caché"),
    ("de", "dup", "SQLite für die lokale Speicherung verwenden", "SQLite für die lokale Speicherschicht verwenden"),
    ("de", "diff", "Redis für den Cache verwenden", "Redis nicht für den Cache verwenden"),
    ("zh", "dup", "本地存储使用SQLite数据库", "本地存储层使用SQLite数据库"),
    ("zh", "diff", "缓存使用Redis", "缓存不要使用Redis"),
    ("zh", "none", "本地存储使用SQLite数据库", "发布分支按版本号命名"),
    ("ja", "dup", "ローカル保存にはSQLiteを使う", "ローカル保存層にはSQLiteを使う"),
    ("ja", "diff", "キャッシュにRedisを使う", "キャッシュにRedisを使わない"),
]

# (label, new decision, active decision, options the active one rejected)
CONFLICTS: list[tuple[str, str, str, list[str]]] = [
    ("conflict", "Do not use Redis for the session cache", "Use Redis for the session cache", []),
    ("conflict", "Retry failed uploads 5 times", "Retry failed uploads 3 times", []),
    ("conflict", "Never run migrations during deploy", "Run migrations during deploy", []),
    ("conflict", "Support Python 3.12 only", "Support Python 3.10 and newer", []),
    ("conflict", "Avoid mocks in the storage tests", "Use mocks in the storage tests", []),
    ("conflict", "Keep 7 days of logs", "Keep 30 days of logs", []),
    ("rejected", "Use Postgres for local storage", "Use SQLite for local storage", ["Postgres"]),
    ("rejected", "Store the JWT in localStorage", "Store the JWT in httpOnly cookies", ["localStorage"]),
    ("rejected", "Use litellm for the helper model calls", "Call the helper model with plain HTTP", ["litellm"]),
    ("none", "Use SQLite for local storage", "Use SQLite for local storage tests too", []),
    ("none", "Log structured JSON from the CLI", "Run ruff in CI before the tests", []),
    ("none", "Use Redis for the session cache", "Pin the docker base image by digest", []),
    ("none", "Cache embeddings on disk", "Retry failed uploads 3 times", []),
    ("none", "Keep the MCP tool descriptions short", "Keep 30 days of logs", []),
]

_DUP_NAMES = {"possible-duplicate": "dup", "protected-difference": "diff"}
_CONFLICT_NAMES = {"possible-conflict": "conflict", "rejected-alternative": "rejected"}


def classify(a: str, b: str, dup_t: float, rel_t: float) -> str:
    dedup.DUPLICATE_SCORE, dedup.RELATED_SCORE = dup_t, rel_t
    relation = dedup.candidate_relation(a, b)[0]
    return _DUP_NAMES.get(relation, relation)


def _scores(pairs, dup_t: float, rel_t: float) -> dict[str, float]:
    got = [(label, classify(a, b, dup_t, rel_t)) for label, a, b in pairs]
    tp = sum(lab == "dup" and g == "dup" for lab, g in got)
    fp = sum(lab != "dup" and g == "dup" for lab, g in got)
    fn = sum(lab == "dup" and g != "dup" for lab, g in got)
    return {"precision": tp / (tp + fp) if tp + fp else 1.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "accuracy": sum(lab == g for lab, g in got) / len(got)}


def _conflict_label(new: str, old: str, rejected: list[str]) -> str:
    return _CONFLICT_NAMES.get(dedup.conflict_relation(new, old, rejected)[0], "none")


def _conflicts(threshold: float) -> dict[str, float]:
    dedup.CONFLICT_OVERLAP = threshold
    got = [(lab, _conflict_label(new, old, rej)) for lab, new, old, rej in CONFLICTS]
    flagged = [(lab, g) for lab, g in got if g != "none"]
    real = [(lab, g) for lab, g in got if lab != "none"]
    return {"precision": sum(lab == g for lab, g in flagged) / len(flagged) if flagged else 1.0,
            "recall": sum(lab == g for lab, g in real) / len(real),
            "accuracy": sum(lab == g for lab, g in got) / len(got)}


def _fmt(s: dict[str, float]) -> str:
    return f"precision={s['precision']:.2f} recall={s['recall']:.2f} accuracy={s['accuracy']:.2f}"


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    dup_now, rel_now, conflict_now = dedup.DUPLICATE_SCORE, dedup.RELATED_SCORE, dedup.CONFLICT_OVERLAP
    print(f"duplicates: {len(PAIRS)} tuning pairs, {len(HELD_OUT)} held-out pairs")
    print(f"{'dup_t':>6} {'rel_t':>6} {'precision':>10} {'recall':>7} {'accuracy':>9} {'held-out acc':>13}")
    best = None
    for dup_t in (0.4, 0.45, 0.5, 0.55, 0.6, 0.7, 0.75):
        for rel_t in (0.15, 0.2, 0.25, 0.3, 0.45):
            if rel_t >= dup_t:
                continue
            s, h = _scores(PAIRS, dup_t, rel_t), _scores(HELD_OUT, dup_t, rel_t)
            print(f"{dup_t:>6} {rel_t:>6} {s['precision']:>10.2f} {s['recall']:>7.2f} "
                  f"{s['accuracy']:>9.2f} {h['accuracy']:>13.2f}")
            key = (s["precision"] >= 0.95, s["accuracy"], s["recall"])
            if best is None or key > best[0]:
                best = (key, dup_t, rel_t)
    _, d, r = best
    print()
    print(f"best on tuning: {d}/{r}  tuning   {_fmt(_scores(PAIRS, d, r))}")
    print(f"                          held-out {_fmt(_scores(HELD_OUT, d, r))}")
    print(f"current: {dup_now}/{rel_now}   held-out {_fmt(_scores(HELD_OUT, dup_now, rel_now))}")
    print("held-out misses at the current setting:")
    for label, a, b in HELD_OUT:
        got = classify(a, b, dup_now, rel_now)
        if got != label:
            print(f"  want {label:7} got {got:7} | {a} || {b}")

    print()
    print(f"other languages at the current setting: {len(MULTILINGUAL)} pairs")
    by_lang: dict[str, list[bool]] = {}
    for lang, label, a, b in MULTILINGUAL:
        got = classify(a, b, dup_now, rel_now)
        by_lang.setdefault(lang, []).append(got == label)
        if got != label:
            print(f"  {lang} want {label:7} got {got:7} | {a} || {b}")
    print("  " + "  ".join(f"{k}: {sum(v)}/{len(v)}" for k, v in by_lang.items()))

    print()
    print(f"conflicts: {len(CONFLICTS)} pairs")
    print(f"{'overlap':>8} {'precision':>10} {'recall':>7} {'accuracy':>9}")
    for t in (0.15, 0.2, 0.25, 0.3, 0.35, 0.45, 0.55):
        s = _conflicts(t)
        print(f"{t:>8} {s['precision']:>10.2f} {s['recall']:>7.2f} {s['accuracy']:>9.2f}")
    s = _conflicts(conflict_now)
    print(f"current {conflict_now}: {_fmt(s)}")
    print("conflict misses at the current setting:")
    for lab, new, old, rej in CONFLICTS:
        got = _conflict_label(new, old, rej)
        if got != lab:
            print(f"  want {lab:8} got {got:8} | {new} || {old}")


if __name__ == "__main__":
    main()
