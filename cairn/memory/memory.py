"""
Hybrid Memory, SQLite + ChromaDB vector search.

Capabilities:
- Async store (queue + worker thread): callers don't block on DB writes.
- Decay scoring: older records lose importance (forgetting curve).
- Sliding window: the most recent N entries get a recency boost.
- Temporal columns: valid_from / valid_until (for invalidate).
"""
from __future__ import annotations

import hashlib
import queue
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from cairn.memory.decay import decayed_score, DEFAULT_HALF_LIFE_DAYS
from cairn.memory import decision_files
from cairn.memory.decision_files import DECISIONS_DIRNAME, new_uid
from cairn.security.guard import mask_secrets


def sqlite_connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


_SENTINEL = object()

LEGACY_SHARED_DB = "~/.cairn/memory.db"


def resolve_db_path(mem_cfg: dict, project_dir: Path | str | None = None) -> Path:
    """Project DB location. Explicit `db_path` wins (relative paths resolve
    against the project dir). Otherwise each project with a `.cairn/` folder
    gets its own DB, so decisions never leak between repos."""
    base = Path(project_dir) if project_dir else Path.cwd()
    explicit = mem_cfg.get("db_path")
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_absolute() else base / p
    if (base / ".cairn").is_dir():
        return base / ".cairn" / "persistence" / "memory.db"
    return Path(LEGACY_SHARED_DB).expanduser()


class Memory:
    def __init__(self, config: dict, project_dir: Path | str | None = None):
        self._config = config  # kept for lazily-constructed helpers (e.g. ConflictDetector)
        self.project_dir = Path(project_dir) if project_dir else Path.cwd()
        cfg = config.get("memory", {})
        self.enabled = cfg.get("enabled", True)
        self.max_results = cfg.get("max_results", 5)
        self.backend = cfg.get("backend", "sqlite")
        db_path = resolve_db_path(cfg, self.project_dir)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path

        # global (cross-project) memory, same schema, separate file
        self.global_enabled = cfg.get("global_enabled", True)
        global_db_path = Path(cfg.get("global_db_path", "~/.cairn/global.db")).expanduser()
        self.global_db_path = global_db_path

        # decay + sliding-window settings
        decay_cfg = cfg.get("decay", {})
        self.decay_enabled = decay_cfg.get("enabled", True)
        self.half_life_days = float(decay_cfg.get("half_life_days", DEFAULT_HALF_LIFE_DAYS))
        self.sliding_window = int(cfg.get("sliding_window", 10))
        self.recency_boost = float(cfg.get("recency_boost", 1.5))

        # dedup
        dedup_cfg = cfg.get("dedup", {})
        self._dedup_cfg = {
            "enabled": dedup_cfg.get("enabled", True),
            "threshold": float(dedup_cfg.get("threshold", 0.92)),
        }

        # secret redaction, best-effort, applied before anything is written
        redact_cfg = cfg.get("redact", {})
        self.redact_enabled = redact_cfg.get("enabled", True)
        self._redact_extra_patterns = redact_cfg.get("extra_patterns", [])

        stale_cfg = cfg.get("stale", {})
        self.stale_enabled = stale_cfg.get("enabled", True)
        self.stale_commits = int(stale_cfg.get("commits", 10))
        self._git_cache: dict[tuple, int | None] = {}

        # async store
        self.async_store_enabled = cfg.get("async_store", True)
        self._write_queue: "queue.Queue" = queue.Queue()
        self._writer_thread: threading.Thread | None = None

        self._session: list[dict] = []
        self._chroma = None
        self._failed_writes = 0

        self.fts5_enabled = False
        self.global_fts5_enabled = False

        if self.enabled:
            self.fts5_enabled = self._init_sqlite(self.db_path)
            if self.backend == "hybrid":
                self._init_chroma(db_path.parent)
            if self.global_enabled:
                global_db_path.parent.mkdir(parents=True, exist_ok=True)
                self.global_fts5_enabled = self._init_sqlite(global_db_path)
            if self.async_store_enabled:
                self._start_writer()

    # ------------------------- init / lifecycle -------------------------

    def _init_sqlite(self, db_path: Path) -> bool:
        with sqlite_connect(db_path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                session_id TEXT,
                prompt TEXT NOT NULL,
                response TEXT NOT NULL,
                keywords TEXT,
                importance INTEGER DEFAULT 1,
                content_hash TEXT,
                valid_from TEXT,
                valid_until TEXT,
                invalidated_reason TEXT
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kw ON memories(keywords)")
            conn.commit()
            self._migrate(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_hash ON memories(content_hash)")
            conn.commit()
            return self._init_fts5(conn)

    def _init_fts5(self, conn: sqlite3.Connection) -> bool:
        fts5_enabled = False
        try:
            conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                prompt,
                response,
                content=memories,
                content_rowid=id
            )""")
            conn.execute("""CREATE TRIGGER IF NOT EXISTS trg_memories_ai AFTER INSERT ON memories BEGIN
              INSERT INTO memories_fts(rowid, prompt, response) VALUES (new.id, new.prompt, new.response);
            END;""")
            conn.execute("""CREATE TRIGGER IF NOT EXISTS trg_memories_ad AFTER DELETE ON memories BEGIN
              INSERT INTO memories_fts(memories_fts, rowid, prompt, response) VALUES('delete', old.id, old.prompt, old.response);
            END;""")
            conn.execute("""CREATE TRIGGER IF NOT EXISTS trg_memories_au AFTER UPDATE ON memories BEGIN
              INSERT INTO memories_fts(memories_fts, rowid, prompt, response) VALUES('delete', old.id, old.prompt, old.response);
              INSERT INTO memories_fts(rowid, prompt, response) VALUES (new.id, new.prompt, new.response);
            END;""")
            
            count_mem = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            count_fts = conn.execute("SELECT COUNT(*) FROM memories_fts").fetchone()[0]
            if count_mem > 0 and count_fts == 0:
                conn.execute("INSERT INTO memories_fts(rowid, prompt, response) SELECT id, prompt, response FROM memories")
            conn.commit()
            fts5_enabled = True
        except sqlite3.OperationalError as e:
            import sys
            print(f"FTS5 initialization warning (using legacy fallback): {e}", file=sys.stderr)
        return fts5_enabled

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Add temporal/dedup/provenance columns + evidence table (idempotent)."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()}
        adds = [
            ("content_hash", "TEXT"),
            ("valid_from", "TEXT"),
            ("valid_until", "TEXT"),
            ("invalidated_reason", "TEXT"),
            ("kind", "TEXT DEFAULT 'memory'"),
            ("reasoning", "TEXT"),
            ("alternatives_json", "TEXT"),
            ("superseded_by", "INTEGER"),
            ("chose_because", "TEXT"),
            ("scope_path", "TEXT"),
            ("uid", "TEXT"),
            ("anchor_commit", "TEXT"),
        ]
        for col, typ in adds:
            if col not in existing:
                conn.execute(f"ALTER TABLE memories ADD COLUMN {col} {typ}")
        conn.execute("""CREATE TABLE IF NOT EXISTS memory_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            memory_id INTEGER NOT NULL,
            source TEXT,
            claim TEXT,
            FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_mem ON memory_evidence(memory_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_kind ON memories(kind)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_superseded_by ON memories(superseded_by)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_scope_path ON memories(scope_path)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_uid ON memories(uid)")
        conn.commit()

    def _init_chroma(self, base_dir: Path):
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(base_dir / "chroma"))
            self._chroma = client.get_or_create_collection(
                name="cairn_memory",
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as e:
            import sys
            print(f"Chroma init error: {e}", file=sys.stderr)
            self._failed_writes += 1

    def _start_writer(self) -> None:
        t = threading.Thread(target=self._writer_loop, name="cairn-memory-writer", daemon=True)
        t.start()
        self._writer_thread = t

    def _writer_loop(self) -> None:
        while True:
            item = self._write_queue.get()
            try:
                if item is _SENTINEL:
                    return
                self._write_entry(item)
            except Exception as e:
                import sys
                print(f"Async writer error: {e}", file=sys.stderr)
                self._failed_writes += 1
            finally:
                self._write_queue.task_done()

    def flush(self, timeout: float | None = None) -> None:
        """Block until all pending writes complete (for tests/CLI)."""
        if not self.async_store_enabled:
            return
        if timeout is None:
            self._write_queue.join()
        else:
            # queue.Queue.join() does not support a timeout; poll manually
            import time
            deadline = time.monotonic() + timeout
            while not self._write_queue.unfinished_tasks == 0:
                if time.monotonic() > deadline:
                    return
                time.sleep(0.01)

    def close(self) -> None:
        if self._writer_thread and self._writer_thread.is_alive():
            self._write_queue.put(_SENTINEL)
            self._writer_thread.join(timeout=2.0)

    # ------------------------- store -------------------------

    @staticmethod
    def _content_hash(prompt: str, response: str) -> str:
        h = hashlib.sha1()
        h.update(prompt.strip().lower().encode("utf-8", errors="ignore"))
        h.update(b"\x1f")
        h.update(response.strip()[:1000].encode("utf-8", errors="ignore"))
        return h.hexdigest()

    @staticmethod
    def _normalize_path(path: str) -> str:
        return path.replace("\\", "/").strip("/")

    @classmethod
    def _path_filter_sql(cls, path: str | None, column: str = "scope_path") -> tuple[str, tuple]:
        """SQL fragment + params scoping a query to a workspace-relative path.

        A row with scope_path NULL is repo-wide and always matches. A row
        scoped to an ancestor directory of `path` (or to `path` itself)
        matches; a sibling or descendant-of-a-sibling does not.
        """
        if not path:
            return "", ()
        norm = cls._normalize_path(path)
        return (
            f" AND ({column} IS NULL OR {column} = ? OR ? LIKE ({column} || '/%'))",
            (norm, norm),
        )

    @staticmethod
    def _validate_scope(scope: str) -> None:
        if scope not in ("project", "global"):
            raise ValueError(f"invalid scope: {scope!r} (expected 'project' or 'global')")

    def _scope_db_path(self, scope: str) -> Path:
        if scope == "global":
            if not self.global_enabled:
                raise ValueError("global memory is disabled (config: memory.global_enabled)")
            return self.global_db_path
        return self.db_path

    def _redact(self, text: str | None) -> str | None:
        """Best-effort secret redaction. Not a guarantee, see README security note."""
        if text is None or not self.redact_enabled:
            return text
        return mask_secrets(text, extra_patterns=self._redact_extra_patterns)

    def store(
        self,
        prompt: str,
        response: str,
        importance: int = 1,
        scope_path: str | None = None,
        scope: str = "project",
    ):
        if not self.enabled:
            return
        self._validate_scope(scope)
        self._scope_db_path(scope)  # raises early if global disabled
        prompt = self._redact(prompt)
        response = self._redact(response)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "prompt": prompt,
            "response": response,
            "keywords": self._extract_keywords(prompt + " " + response[:1000]),
            "importance": importance,
            "content_hash": self._content_hash(prompt, response),
            "scope_path": self._normalize_path(scope_path) if scope_path else None,
            "scope": scope,
        }
        self._session.append(entry)
        if self.async_store_enabled:
            self._write_queue.put(entry)
        else:
            self._write_entry(entry)

    def _write_entry(self, entry: dict) -> None:
        scope = entry.get("scope", "project")
        target_db = self._scope_db_path(scope)

        # Dedup hook (project-scoped only, dedup.py compares against
        # self.db_path, which would be wrong for global-scope entries)
        if scope == "project":
            try:
                from cairn.memory.dedup import maybe_merge
                if maybe_merge(self, entry):
                    return
            except ImportError:
                pass

        with sqlite_connect(target_db) as conn:
            cursor = conn.execute(
                "INSERT INTO memories (timestamp, prompt, response, keywords, importance, "
                "content_hash, valid_from, scope_path) VALUES (?,?,?,?,?,?,?,?)",
                (
                    entry["timestamp"],
                    entry["prompt"],
                    entry["response"][:3000],
                    entry["keywords"],
                    entry["importance"],
                    entry["content_hash"],
                    entry["timestamp"],
                    entry.get("scope_path"),
                ),
            )
            row_id = cursor.lastrowid
            conn.commit()
        entry["id"] = row_id
        if scope == "project" and self._chroma:
            try:
                self._chroma.add(
                    documents=[f"{entry['prompt']}\n{entry['response'][:500]}"],
                    ids=[str(row_id)],
                    metadatas=[{"prompt": entry["prompt"][:200], "timestamp": entry["timestamp"]}],
                )
            except Exception as e:
                import sys
                print(f"Chroma add error: {e}", file=sys.stderr)
                self._failed_writes += 1

    # ------------------------- recall -------------------------

    def recall(self, prompt: str, limit: int | None = None, path: str | None = None) -> str:
        if not self.enabled:
            return ""
        limit = limit or self.max_results
        results: list[dict] = []
        path_sql, path_params = self._path_filter_sql(path)
        if self._chroma:
            try:
                res = self._chroma.query(query_texts=[prompt], n_results=min(limit, 3))
                if res and "documents" in res and res["documents"] and len(res["documents"]) > 0:
                    for doc, meta, doc_id in zip(res["documents"][0], res["metadatas"][0], res["ids"][0]):
                        try:
                            db_id = int(doc_id)
                            with sqlite_connect(self.db_path) as conn:
                                row = conn.execute(
                                    "SELECT kind, timestamp, prompt, response FROM memories "
                                    "WHERE id = ? AND valid_until IS NULL" + path_sql,
                                    (db_id, *path_params),
                                ).fetchone()
                            if row:
                                results.append({
                                    "id": db_id,
                                    "kind": row[0] or "memory",
                                    "timestamp": row[1],
                                    "prompt": row[2],
                                    "response": row[3],
                                    "content": f"Q: {row[2]}\nA: {row[3]}",
                                    "scope": "project",
                                    "score": float("inf"),  # semantic top-matches keep priority
                                })
                        except (ValueError, TypeError):
                            pass
            except Exception as e:
                import sys
                print(f"Chroma query error: {e}", file=sys.stderr)

        for r in self._sqlite_keyword_search(prompt, limit=limit, path=path):
            if not any(r["id"] == x.get("id") for x in results):
                results.append({
                    "id": r["id"],
                    "kind": r["kind"],
                    "timestamp": r["timestamp"],
                    "prompt": r["prompt"],
                    "response": r["response"],
                    "content": f"Q: {r['prompt']}\nA: {r['response']}",
                    "scope": "project",
                    "score": r.get("score", 0.0),
                })

        if self.global_enabled:
            for r in self._sqlite_keyword_search(
                prompt, limit=limit, db_path=self.global_db_path, fts5_enabled=self.global_fts5_enabled
            ):
                results.append({
                    "id": r["id"],
                    "kind": r["kind"],
                    "timestamp": r["timestamp"],
                    "prompt": r["prompt"],
                    "response": r["response"],
                    "content": f"Q: {r['prompt']}\nA: {r['response']}",
                    "scope": "global",
                    "score": r.get("score", 0.0),
                })
            # Re-rank the combined set so global results interleave by
            # score rather than always trailing project results.
            results.sort(key=lambda r: r.get("score", 0.0), reverse=True)

        if not results:
            return ""
        formatted = []
        for r in results[:limit]:
            date_str = "unknown"
            if r["timestamp"]:
                date_str = r["timestamp"][:10]
            display_id = f"g{r['id']}" if r.get("scope") == "global" else r["id"]
            tag = " · global" if r.get("scope") == "global" else ""
            flag = ""
            if r.get("scope") == "project" and r["kind"] == "decision":
                st = self.staleness(r["id"])
                if st:
                    flag = f" · NEEDS REVIEW: {st['commits_since']} commits touched {st['path']} since recorded"
            header = f"[#{display_id} · {r['kind']} · {date_str}{tag}{flag}]"
            content = r["content"][:600]
            formatted.append(f"{header}\n{content}")
        return "\n---\n".join(formatted)

    def _sqlite_keyword_search(
        self,
        prompt: str,
        limit: int = 5,
        path: str | None = None,
        db_path: Path | None = None,
        fts5_enabled: bool | None = None,
    ) -> list[dict]:
        db_path = db_path or self.db_path
        if fts5_enabled is None:
            fts5_enabled = self.fts5_enabled if db_path == self.db_path else self.global_fts5_enabled
        path_sql, path_params = self._path_filter_sql(path, column="memories.scope_path")
        if fts5_enabled:
            # FTS5 search path
            keywords = [w for w in self._extract_keywords(prompt).split(",") if w]
            if not keywords:
                return []
            match_query = " OR ".join(keywords)
            with sqlite_connect(db_path) as conn:
                try:
                    rows = conn.execute(
                        "SELECT id, prompt, response, timestamp, kind, importance, -bm25(memories_fts) AS score_bm25 "
                        "FROM memories JOIN memories_fts ON memories.id = memories_fts.rowid "
                        "WHERE memories.valid_until IS NULL AND memories_fts MATCH ?" + path_sql + " "
                        "ORDER BY score_bm25 DESC LIMIT 100",
                        (match_query, *path_params),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if rows:
                with sqlite_connect(db_path) as conn:
                    recent_rows = conn.execute(
                        "SELECT id FROM memories WHERE valid_until IS NULL ORDER BY id DESC LIMIT ?",
                        (self.sliding_window,),
                    ).fetchall()
                recent_ids = {r[0] for r in recent_rows}
                now = datetime.now(timezone.utc)
                scored = []
                for rid, rp, rr, ts, rknd, imp, bm25_val in rows:
                    base_imp = imp or 1
                    score = decayed_score(base_imp, ts, self.half_life_days, now) \
                        if self.decay_enabled else float(base_imp)
                    if rid in recent_ids:
                        score *= self.recency_boost
                    score *= (1.0 + max(0.0, bm25_val))
                    scored.append((score, {
                        "id": rid,
                        "prompt": rp,
                        "response": rr,
                        "timestamp": ts,
                        "kind": rknd or "memory",
                        "score": score,
                    }))
                scored.sort(reverse=True, key=lambda x: x[0])
                return [item for _, item in scored[:limit]]

        # Legacy keyword search fallback
        legacy_path_sql, legacy_path_params = self._path_filter_sql(path)
        keywords = set(self._extract_keywords(prompt).split(","))
        with sqlite_connect(db_path) as conn:
            rows = conn.execute(
                "SELECT id, prompt, response, keywords, importance, timestamp, kind "
                "FROM memories WHERE valid_until IS NULL" + legacy_path_sql + " "
                "ORDER BY id DESC LIMIT 400",
                legacy_path_params,
            ).fetchall()

        recent_ids = {row[0] for row in rows[: self.sliding_window]}
        now = datetime.now(timezone.utc)

        scored = []
        for rid, rp, rr, rk, imp, ts, rknd in rows:
            if not rk:
                continue
            overlap = len(keywords & set(rk.split(",")))
            if overlap == 0:
                continue
            base_imp = imp or 1
            score = decayed_score(base_imp, ts, self.half_life_days, now) \
                if self.decay_enabled else float(base_imp)
            if rid in recent_ids:
                score *= self.recency_boost
            score *= overlap
            scored.append((score, {
                "id": rid,
                "prompt": rp,
                "response": rr,
                "timestamp": ts,
                "kind": rknd or "memory",
                "score": score,
            }))
        scored.sort(reverse=True, key=lambda x: x[0])
        return [item for _, item in scored[:limit]]

    def recent(self, n: int = 10) -> list[dict]:
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT timestamp, prompt, response FROM memories "
                "WHERE valid_until IS NULL ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()
        return [{"timestamp": r[0], "prompt": r[1], "response": r[2]} for r in rows]

    def search(self, query: str, limit: int = 10, path: str | None = None) -> list[dict]:
        path_sql, path_params = self._path_filter_sql(path)
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT timestamp, prompt, response FROM memories "
                "WHERE (prompt LIKE ? OR response LIKE ?) AND valid_until IS NULL" + path_sql + " "
                "ORDER BY id DESC LIMIT ?",
                (f"%{query}%", f"%{query}%", *path_params, limit),
            ).fetchall()
        results = [{"timestamp": r[0], "prompt": r[1], "response": r[2], "scope": "project"} for r in rows]

        if self.global_enabled:
            with sqlite_connect(self.global_db_path) as conn:
                g_rows = conn.execute(
                    "SELECT timestamp, prompt, response FROM memories "
                    "WHERE (prompt LIKE ? OR response LIKE ?) AND valid_until IS NULL "
                    "ORDER BY id DESC LIMIT ?",
                    (f"%{query}%", f"%{query}%", limit),
                ).fetchall()
            results.extend(
                {"timestamp": r[0], "prompt": r[1], "response": r[2], "scope": "global"} for r in g_rows
            )
            results.sort(key=lambda r: r["timestamp"] or "", reverse=True)

        return results[:limit]

    def clear(self) -> int:
        self.flush()
        with sqlite_connect(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            conn.execute("DELETE FROM memories")
            conn.commit()
        self._session.clear()
        return count

    def save_session(self, session_id: str):
        if not self._session:
            return
        self.flush()
        with sqlite_connect(self.db_path) as conn:
            for e in self._session:
                row_id = e.get("id")
                if row_id is not None:
                    conn.execute(
                        "UPDATE memories SET session_id=? WHERE id=?",
                        (session_id, row_id),
                    )
            conn.commit()

    # ------------------------- decision provenance -------------------------

    def _resolve_scope_id(self, mem_id: int | str) -> tuple[Path, int]:
        """Map a public id ("g<n>" or plain int) to (db_path, raw int id)."""
        s = str(mem_id)
        if s.startswith("g") and s[1:].isdigit():
            if not self.global_enabled:
                raise ValueError("global memory is disabled (config: memory.global_enabled)")
            return self.global_db_path, int(s[1:])
        return self.db_path, int(mem_id)

    def check_conflicts(self, prompt: str, response: str) -> list[dict]:
        """Opt-in (`memory.conflict.enabled`, default false): ask the helper LLM
        whether `prompt`/`response` contradicts a prior high-importance record
        (project or global). Returns `[{"id", "reason"}, ...]`, empty if the
        feature is off or nothing conflicts. Never invalidates anything itself -
        see `cairn/memory/conflict.py` for the false-positive rationale."""
        from cairn.memory.conflict import ConflictDetector
        detector = ConflictDetector(self._config)
        return detector.detect(self, prompt, response)

    def record_decision(
        self,
        decision: str,
        reasoning: str | None = None,
        alternatives: list[dict] | None = None,
        evidence: list[dict] | None = None,
        supersedes: int | str | None = None,
        chose_because: str | None = None,
        importance: int = 2,
        scope_path: str | None = None,
        scope: str = "project",
    ) -> int | str:
        """Insert a decision row synchronously and return its id.

        alternatives: [{"option": str, "rejected_because": str}, ...]
        evidence:     [{"source": str, "claim": str}, ...]
        supersedes:   prior decision id (plain int for project, "g<id>" for
                      global); if set, that row is invalidated and its
                      superseded_by is filled.
        scope_path:   workspace-relative path this decision applies to (e.g.
                      "apps/api"); None = repo-wide. Only meaningful for
                      scope="project".
        scope:        "project" (default, this project's .cairn/) or
                      "global" (~/.cairn/global.db, cross-project).
        """
        import json as _json

        self._validate_scope(scope)
        target_db = self._scope_db_path(scope)
        is_global = scope == "global"

        decision = self._redact(decision)
        reasoning = self._redact(reasoning)
        chose_because = self._redact(chose_because)
        alternatives = [
            {"option": self._redact(a.get("option")), "rejected_because": self._redact(a.get("rejected_because"))}
            for a in (alternatives or [])
        ] or None
        evidence = [
            {"source": self._redact(e.get("source")), "claim": self._redact(e.get("claim"))}
            for e in (evidence or [])
        ] or None

        self.flush()
        now = datetime.now(timezone.utc).isoformat()
        alts_json = _json.dumps(alternatives or [], ensure_ascii=False) if alternatives else None
        norm_scope = self._normalize_path(scope_path) if scope_path else None

        with sqlite_connect(target_db) as conn:
            cursor = conn.execute(
                "INSERT INTO memories (timestamp, prompt, response, keywords, importance, "
                "content_hash, valid_from, kind, reasoning, alternatives_json, chose_because, scope_path, "
                "uid, anchor_commit) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    now,
                    decision,
                    reasoning or "",
                    self._extract_keywords(decision + " " + (reasoning or "")),
                    importance,
                    self._content_hash(decision, reasoning or ""),
                    now,
                    "decision",
                    reasoning,
                    alts_json,
                    chose_because,
                    norm_scope,
                    new_uid(datetime.fromisoformat(now)),
                    None if is_global else self._git_head(),
                ),
            )
            new_id = cursor.lastrowid
            for ev in evidence or []:
                conn.execute(
                    "INSERT INTO memory_evidence (memory_id, source, claim) VALUES (?,?,?)",
                    (new_id, ev.get("source"), ev.get("claim")),
                )
            conn.commit()

            superseded_ok = False
            sup_db = None
            sup_num_id = None
            if supersedes is not None:
                sup_db, sup_num_id = self._resolve_scope_id(supersedes)
                supersede_marker = f"g{new_id}" if is_global else new_id
                if sup_db == target_db:
                    cur = conn.execute(
                        "UPDATE memories SET valid_until=?, invalidated_reason=?, superseded_by=? "
                        "WHERE id=? AND valid_until IS NULL",
                        (now, f"superseded by #{supersede_marker}", new_id, sup_num_id),
                    )
                    conn.commit()
                    superseded_ok = cur.rowcount > 0

        # cross-scope supersede (e.g. a global decision superseding a
        # project one, or vice versa) needs its own connection
        if supersedes is not None and sup_db is not None and sup_db != target_db:
            supersede_marker = f"g{new_id}" if is_global else new_id
            with sqlite_connect(sup_db) as sup_conn:
                cur = sup_conn.execute(
                    "UPDATE memories SET valid_until=?, invalidated_reason=?, superseded_by=? "
                    "WHERE id=? AND valid_until IS NULL",
                    (now, f"superseded by #{supersede_marker}", new_id, sup_num_id),
                )
                sup_conn.commit()
                superseded_ok = cur.rowcount > 0

        if superseded_ok and sup_db == self.db_path and self._chroma:
            try:
                self._chroma.delete(ids=[str(sup_num_id)])
            except Exception as e:
                import sys
                print(f"Chroma delete error for id {sup_num_id}: {e}", file=sys.stderr)
                self._failed_writes += 1

        if not is_global:
            self._export_decision(new_id)
        if superseded_ok and sup_db == self.db_path:
            self._export_decision(sup_num_id)
        return f"g{new_id}" if is_global else new_id

    def get_full(self, mem_id: int | str) -> dict | None:
        """Return a full memory row including evidence + supersedes chain pointers."""
        import json as _json

        db_path, num_id = self._resolve_scope_id(mem_id)
        is_global = db_path == self.global_db_path

        with sqlite_connect(db_path) as conn:
            row = conn.execute(
                "SELECT id, timestamp, prompt, response, keywords, importance, "
                "valid_from, valid_until, invalidated_reason, kind, reasoning, "
                "alternatives_json, superseded_by, chose_because, scope_path "
                "FROM memories WHERE id=?",
                (num_id,),
            ).fetchone()
            if not row:
                return None
            evidence = [
                {"source": s, "claim": c}
                for s, c in conn.execute(
                    "SELECT source, claim FROM memory_evidence WHERE memory_id=?",
                    (num_id,),
                ).fetchall()
            ]
            supersedes_id = conn.execute(
                "SELECT id FROM memories WHERE superseded_by=?", (num_id,)
            ).fetchone()

        def _tag(n):
            return f"g{n}" if is_global and n is not None else n

        out = {
            "id": _tag(row[0]), "timestamp": row[1], "decision": row[2], "response": row[3],
            "keywords": row[4], "importance": row[5], "valid_from": row[6],
            "valid_until": row[7], "invalidated_reason": row[8], "kind": row[9],
            "reasoning": row[10],
            "alternatives": _json.loads(row[11]) if row[11] else [],
            "superseded_by": _tag(row[12]), "chose_because": row[13],
            "scope_path": row[14],
            "scope": "global" if is_global else "project",
            "supersedes": _tag(supersedes_id[0]) if supersedes_id else None,
            "evidence": evidence,
            "stale": None if is_global else self.staleness(num_id),
        }
        return out

    def check_rejected(self, approach: str, limit: int = 5) -> list[dict]:
        """Read-only: has something like `approach` already been rejected or
        abandoned? Scans two sources, project + global DBs:
        (1) decisions' `alternatives_json[].option`, rejected alternatives,
        (2) decisions with `valid_until IS NOT NULL`, abandoned/superseded
        decisions whose own text matches.

        Requires >=2 keyword overlap (not 1) to avoid false positives on a
        single generic shared word; ranked by the same decayed-score used by
        recall(). No LLM call, pure keyword matching, same cost as recall().
        """
        import json as _json

        approach_kw = set(self._extract_keywords(approach).split(","))
        approach_kw.discard("")
        if len(approach_kw) < 2:
            return []

        now = datetime.now(timezone.utc)
        results: list[dict] = []

        def _scan(db_path: Path, is_global: bool) -> None:
            with sqlite_connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT id, prompt, alternatives_json, valid_until, "
                    "invalidated_reason, superseded_by, importance, timestamp "
                    "FROM memories WHERE kind='decision' ORDER BY id DESC LIMIT 500"
                ).fetchall()
            for rid, prompt, alts_json, valid_until, inv_reason, superseded_by, imp, ts in rows:
                display_id = f"g{rid}" if is_global else rid
                base_imp = imp or 1
                score = decayed_score(base_imp, ts, self.half_life_days, now) \
                    if self.decay_enabled else float(base_imp)

                if alts_json:
                    try:
                        alts = _json.loads(alts_json)
                    except (ValueError, TypeError):
                        alts = []
                    for alt in alts:
                        option = alt.get("option") or ""
                        opt_kw = set(self._extract_keywords(option).split(","))
                        if len(approach_kw & opt_kw) >= 2:
                            results.append({
                                "score": score,
                                "kind": "rejected_alternative",
                                "decision_id": display_id,
                                "decision": prompt,
                                "option": option,
                                "reason": alt.get("rejected_because") or "",
                                "scope": "global" if is_global else "project",
                            })

                if valid_until is not None:
                    own_kw = set(self._extract_keywords(prompt).split(","))
                    if len(approach_kw & own_kw) >= 2:
                        sup_by = superseded_by
                        if is_global and sup_by is not None:
                            sup_by = f"g{sup_by}"
                        results.append({
                            "score": score,
                            "kind": "abandoned_decision",
                            "decision_id": display_id,
                            "decision": prompt,
                            "reason": inv_reason or "",
                            "superseded_by": sup_by,
                            "scope": "global" if is_global else "project",
                        })

        _scan(self.db_path, is_global=False)
        if self.global_enabled:
            _scan(self.global_db_path, is_global=True)

        results.sort(key=lambda r: r["score"], reverse=True)
        for r in results:
            r.pop("score", None)
        return results[:limit]

    def thread(self, topic_or_id: str | int, limit: int = 20) -> list[dict]:
        """Chronological list of decision versions on the same topic.

        If int, or a "g<id>" string: walk the supersedes chain (newest
        first → oldest) in the matching DB (project or global).
        If a plain string: keyword-overlap group across project decisions,
        plus global decisions when global memory is enabled.
        """
        rows: list[dict] = []
        is_global_id = isinstance(topic_or_id, str) and topic_or_id.startswith("g") and topic_or_id[1:].isdigit()

        if isinstance(topic_or_id, int) or is_global_id:
            db_path, start_id = self._resolve_scope_id(topic_or_id)
            is_global = db_path == self.global_db_path
            with sqlite_connect(db_path) as conn:
                seen: set[int] = set()
                current: int | None = start_id
                while current is not None and current not in seen and len(rows) < limit:
                    seen.add(current)
                    r = conn.execute(
                        "SELECT id, timestamp, prompt, kind, valid_until, superseded_by "
                        "FROM memories WHERE id=?",
                        (current,),
                    ).fetchone()
                    if not r:
                        break
                    rows.append({
                        "id": f"g{r[0]}" if is_global else r[0], "timestamp": r[1], "decision": r[2],
                        "kind": r[3], "active": r[4] is None, "scope": "global" if is_global else "project",
                    })
                    current = r[5]
            return rows

        keywords = set(self._extract_keywords(str(topic_or_id)).split(","))
        if not keywords:
            return []
        scopes = [(self.db_path, False)]
        if self.global_enabled:
            scopes.append((self.global_db_path, True))
        for db_path, is_global in scopes:
            with sqlite_connect(db_path) as conn:
                all_rows = conn.execute(
                    "SELECT id, timestamp, prompt, keywords, kind, valid_until "
                    "FROM memories WHERE kind='decision' ORDER BY timestamp ASC LIMIT 500"
                ).fetchall()
            for rid, ts, p, kw, kind, vu in all_rows:
                if not kw:
                    continue
                if keywords & set(kw.split(",")):
                    rows.append({
                        "id": f"g{rid}" if is_global else rid, "timestamp": ts, "decision": p,
                        "kind": kind, "active": vu is None, "scope": "global" if is_global else "project",
                    })
                    if len(rows) >= limit:
                        return rows
        return rows

    def decision_audit_score(self, mem_id: int | str) -> dict:
        """Return completeness score for a decision: 0-4 of {reasoning, alternatives, evidence, supersedes}."""
        full = self.get_full(mem_id)
        if not full:
            return {"id": mem_id, "score": 0, "total": 4, "missing": ["not_found"]}
        checks = {
            "has_reasoning": bool(full.get("reasoning")),
            "has_alternatives": bool(full.get("alternatives")),
            "has_evidence": bool(full.get("evidence")),
            "has_supersedes": full.get("supersedes") is not None or full.get("superseded_by") is not None,
        }
        score = sum(1 for v in checks.values() if v)
        missing = [k for k, v in checks.items() if not v]
        return {"id": full["id"], "score": score, "total": 4, "checks": checks, "missing": missing}

    # ------------------------- temporal (γ) -------------------------

    def invalidate(self, mem_id: int | str, reason: str = "") -> bool:
        """Invalidate a record (does not delete; sets valid_until to now)."""
        self.flush()
        now = datetime.now(timezone.utc).isoformat()
        db_path, num_id = self._resolve_scope_id(mem_id)
        is_global = db_path == self.global_db_path
        with sqlite_connect(db_path) as conn:
            cur = conn.execute(
                "UPDATE memories SET valid_until=?, invalidated_reason=? "
                "WHERE id=? AND valid_until IS NULL",
                (now, reason or None, num_id),
            )
            conn.commit()
            ok = cur.rowcount > 0
        if ok:
            if not is_global and self._chroma:
                try:
                    self._chroma.delete(ids=[str(num_id)])
                except Exception as e:
                    import sys
                    print(f"Chroma delete error for id {num_id}: {e}", file=sys.stderr)
                    self._failed_writes += 1
            if not is_global:
                self._export_decision(num_id)
        return ok

    # ------------------------- decision files (git-native) -------------------------

    def decisions_dir(self) -> Path | None:
        """`.cairn/decisions/`, or None when the project has no `.cairn/`."""
        cairn_dir = self.project_dir / ".cairn"
        return cairn_dir / DECISIONS_DIRNAME if cairn_dir.is_dir() else None

    def _git_head(self) -> str | None:
        return self._git("rev-parse", "HEAD") or None

    def _git(self, *args: str) -> str | None:
        import subprocess
        try:
            out = subprocess.run(
                ["git", *args], cwd=self.project_dir,
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    def _commits_since(self, anchor: str, path: str) -> int | None:
        head = self._git("rev-parse", "HEAD")
        if head is None:
            return None
        key = (head, anchor, path)
        if key not in self._git_cache:
            n = self._git("rev-list", "--count", f"{anchor}..HEAD", "--", path)
            self._git_cache[key] = int(n) if n and n.isdigit() else None
        return self._git_cache[key]

    def staleness(self, mem_id: int | str) -> dict | None:
        """{"commits_since", "path", "threshold"} when an active, path-scoped
        project decision has had >= `memory.stale.commits` commits touch its
        path since it was recorded or last confirmed; else None. Repo-wide
        decisions (no path), non-git projects, and anchors that no longer
        exist (rewritten history) are never flagged: no signal beats a wrong one."""
        if not self.stale_enabled:
            return None
        db_path, num_id = self._resolve_scope_id(mem_id)
        if db_path != self.db_path:
            return None
        with sqlite_connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT anchor_commit, scope_path FROM memories "
                "WHERE id=? AND kind='decision' AND valid_until IS NULL",
                (num_id,),
            ).fetchone()
        if not row or not row[0] or not row[1]:
            return None
        n = self._commits_since(row[0], row[1])
        if n is None or n < self.stale_commits:
            return None
        return {"commits_since": n, "path": row[1], "threshold": self.stale_commits}

    def _relative_to_project(self, path: str) -> str:
        p = Path(path)
        if p.is_absolute():
            try:
                p = p.resolve().relative_to(self.project_dir.resolve())
            except ValueError:
                return self._normalize_path(path)
        return self._normalize_path(str(p))

    def decisions_for_path(self, path: str, limit: int = 8) -> list[dict]:
        """Active project decisions scoped to `path` or one of its ancestor
        directories, most important first. Repo-wide decisions are left out
        on purpose: they belong in the session boot summary, not on every edit."""
        rel = self._relative_to_project(path)
        if not rel:
            return []
        path_sql, params = self._path_filter_sql(rel)
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, uid, prompt, reasoning, scope_path, importance FROM memories "
                "WHERE kind='decision' AND valid_until IS NULL AND scope_path IS NOT NULL"
                + path_sql + " ORDER BY importance DESC, id DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [
            {"id": rid, "uid": uid, "decision": d, "reasoning": r, "path": sp,
             "importance": imp, "stale": self.staleness(rid)}
            for rid, uid, d, r, sp, imp in rows
        ]

    def active_decisions(self, min_importance: int = 2, limit: int = 15) -> list[dict]:
        """Compact list of active project decisions for the session boot context."""
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, prompt, scope_path, importance FROM memories "
                "WHERE kind='decision' AND valid_until IS NULL AND importance >= ? "
                "ORDER BY importance DESC, id DESC LIMIT ?",
                (min_importance, limit),
            ).fetchall()
        return [
            {"id": rid, "decision": d, "path": sp, "importance": imp, "stale": self.staleness(rid)}
            for rid, d, sp, imp in rows
        ]

    def stale_decisions(self) -> list[dict]:
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, uid, prompt FROM memories WHERE kind='decision' "
                "AND valid_until IS NULL AND anchor_commit IS NOT NULL AND scope_path IS NOT NULL "
                "ORDER BY id"
            ).fetchall()
        out = []
        for rid, uid, prompt in rows:
            st = self.staleness(rid)
            if st:
                out.append({"id": rid, "uid": uid, "decision": prompt, **st})
        out.sort(key=lambda r: r["commits_since"], reverse=True)
        return out

    def confirm_decision(self, mem_id: int | str) -> bool:
        """Mark a decision as still valid: re-anchor it to the current HEAD."""
        db_path, num_id = self._resolve_scope_id(mem_id)
        if db_path != self.db_path:
            raise ValueError("only project decisions track staleness")
        head = self._git_head()
        if head is None:
            raise ValueError("not a git repository (or no commits yet)")
        with sqlite_connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE memories SET anchor_commit=? WHERE id=? AND kind='decision'",
                (head, num_id),
            )
            conn.commit()
        if cur.rowcount:
            self._export_decision(num_id)
        return cur.rowcount > 0

    def _decision_as_file_dict(self, conn: sqlite3.Connection, num_id: int) -> dict | None:
        import json as _json
        row = conn.execute(
            "SELECT uid, timestamp, prompt, reasoning, alternatives_json, chose_because, "
            "importance, scope_path, valid_until, invalidated_reason, superseded_by, anchor_commit "
            "FROM memories WHERE id=? AND kind='decision'",
            (num_id,),
        ).fetchone()
        if not row:
            return None
        (uid, ts, decision, reasoning, alts, chose, imp, path, valid_until,
         inv_reason, superseded_by, anchor) = row
        if not uid:
            uid = new_uid(datetime.fromisoformat(ts))
            conn.execute("UPDATE memories SET uid=? WHERE id=?", (uid, num_id))
            conn.commit()
        sup_by_uid = None
        if superseded_by is not None:
            r = conn.execute("SELECT uid FROM memories WHERE id=?", (superseded_by,)).fetchone()
            sup_by_uid = r[0] if r else None
        prev = conn.execute(
            "SELECT uid FROM memories WHERE superseded_by=? AND kind='decision'", (num_id,)
        ).fetchone()
        status = "active"
        if valid_until:
            status = "superseded" if superseded_by is not None else "invalidated"
        return {
            "uid": uid, "date": ts, "status": status, "importance": imp,
            "path": path, "supersedes": prev[0] if prev else None,
            "superseded_by": sup_by_uid,
            "valid_until": valid_until,
            "invalidated_reason": None if superseded_by is not None else inv_reason,
            "anchor_commit": anchor,
            "decision": decision, "reasoning": reasoning, "chose_because": chose,
            "alternatives": _json.loads(alts) if alts else [],
            "evidence": [
                {"source": s, "claim": c}
                for s, c in conn.execute(
                    "SELECT source, claim FROM memory_evidence WHERE memory_id=? ORDER BY id",
                    (num_id,),
                ).fetchall()
            ],
        }

    def _export_decision(self, num_id: int) -> Path | None:
        directory = self.decisions_dir()
        if directory is None:
            return None
        with sqlite_connect(self.db_path) as conn:
            d = self._decision_as_file_dict(conn, num_id)
        if d is None:
            return None
        directory.mkdir(parents=True, exist_ok=True)
        text = decision_files.render(d)
        target = decision_files.find_file(directory, d["uid"]) or (
            directory / f"{d['uid']}-{decision_files.slugify(d['decision'])}.md"
        )
        if not target.exists() or target.read_text(encoding="utf-8") != text:
            target.write_text(text, encoding="utf-8", newline="\n")
        return target

    def sync_decision_files(self) -> dict:
        """Two-way sync between `.cairn/decisions/*.md` and the project DB.

        Files win: a file whose content differs from the DB (hand edit, or a
        teammate's decision pulled via git) is imported. DB decisions with no
        file (new records, pre-files databases) are exported. Files are never
        deleted; to retire a decision, invalidate or supersede it."""
        import sys
        counts = {"imported": 0, "updated": 0, "exported": 0, "errors": 0}
        directory = self.decisions_dir()
        if directory is None or not self.enabled:
            return counts
        self.flush()
        parsed: list[dict] = []
        if directory.is_dir():
            for f in sorted(directory.glob("*.md")):
                try:
                    parsed.append(decision_files.parse(f.read_text(encoding="utf-8")))
                except (ValueError, OSError) as e:
                    counts["errors"] += 1
                    print(f"cairn: skipping unreadable decision file {f.name}: {e}", file=sys.stderr)

        with sqlite_connect(self.db_path) as conn:
            for d in parsed:
                row = conn.execute("SELECT id FROM memories WHERE uid=?", (d["uid"],)).fetchone()
                if row is None:
                    self._insert_decision_from_file(conn, d)
                    counts["imported"] += 1
                    continue
                current = self._decision_as_file_dict(conn, row[0]) or {}
                if decision_files.render(current) != decision_files.render(d):
                    self._update_decision_from_file(conn, row[0], d)
                    counts["updated"] += 1
            for d in parsed:
                self._link_supersedes_from_file(conn, d)
            conn.commit()
            file_uids = {d["uid"] for d in parsed}
            missing = [
                rid for rid, uid in conn.execute(
                    "SELECT id, uid FROM memories WHERE kind='decision'"
                ).fetchall()
                if uid is None or uid not in file_uids
            ]
        for rid in missing:
            if self._export_decision(rid):
                counts["exported"] += 1
        return counts

    def import_adrs(self, adr_dir: Path) -> dict:
        """Copy ADRs (Nygard/MADR) into `.cairn/decisions/` and index them.
        Proposed/draft ADRs are skipped; already-imported ones are left alone."""
        directory = self.decisions_dir()
        if directory is None:
            raise ValueError("no .cairn/ folder here; run `cairn init` first")
        directory.mkdir(parents=True, exist_ok=True)
        counts = {"imported": 0, "skipped": 0}
        now = datetime.now(timezone.utc)
        for f in sorted(Path(adr_dir).glob("*.md")):
            d = decision_files.adr_to_decision(f.read_text(encoding="utf-8"), f.name, now)
            if d is None or decision_files.find_file(directory, d["uid"]):
                counts["skipped"] += 1
                continue
            d["decision"] = self._redact(d["decision"])
            d["reasoning"] = self._redact(d["reasoning"])
            target = directory / f"{d['uid']}-{decision_files.slugify(d['decision'].splitlines()[0])}.md"
            target.write_text(decision_files.render(d), encoding="utf-8", newline="\n")
            counts["imported"] += 1
        self.sync_decision_files()
        return counts

    def reindex_decisions(self) -> dict:
        """Drop decision rows from the project DB and rebuild them from files."""
        directory = self.decisions_dir()
        if directory is None or not directory.is_dir():
            raise ValueError("no .cairn/decisions/ folder to rebuild from")
        self.flush()
        with sqlite_connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM memory_evidence WHERE memory_id IN "
                "(SELECT id FROM memories WHERE kind='decision')"
            )
            conn.execute("DELETE FROM memories WHERE kind='decision'")
            conn.commit()
        return self.sync_decision_files()

    @staticmethod
    def _file_validity(d: dict) -> tuple[str | None, str | None]:
        if d["status"] == "active":
            return None, None
        valid_until = d.get("valid_until") or datetime.now(timezone.utc).isoformat()
        reason = d.get("invalidated_reason")
        if d["status"] == "superseded" and d.get("superseded_by"):
            reason = f"superseded by {d['superseded_by']}"
        return valid_until, reason

    def _decision_columns_from_file(self, d: dict) -> dict:
        import json as _json
        valid_until, reason = self._file_validity(d)
        reasoning = d.get("reasoning")
        alts = d.get("alternatives")
        return {
            "prompt": d["decision"],
            "response": reasoning or "",
            "keywords": self._extract_keywords(d["decision"] + " " + (reasoning or "")),
            "importance": d.get("importance") or 2,
            "content_hash": self._content_hash(d["decision"], reasoning or ""),
            "reasoning": reasoning,
            "alternatives_json": _json.dumps(alts, ensure_ascii=False) if alts else None,
            "chose_because": d.get("chose_because"),
            "scope_path": self._normalize_path(d["path"]) if d.get("path") else None,
            "valid_until": valid_until,
            "invalidated_reason": reason,
            "anchor_commit": d.get("anchor_commit"),
        }

    @staticmethod
    def _replace_evidence(conn: sqlite3.Connection, num_id: int, evidence: list[dict]) -> None:
        conn.execute("DELETE FROM memory_evidence WHERE memory_id=?", (num_id,))
        for ev in evidence:
            conn.execute(
                "INSERT INTO memory_evidence (memory_id, source, claim) VALUES (?,?,?)",
                (num_id, ev.get("source"), ev.get("claim")),
            )

    def _insert_decision_from_file(self, conn: sqlite3.Connection, d: dict) -> int:
        cols = self._decision_columns_from_file(d)
        date = d.get("date") or datetime.now(timezone.utc).isoformat()
        cols.update({"timestamp": date, "valid_from": date, "kind": "decision", "uid": d["uid"]})
        cur = conn.execute(
            f"INSERT INTO memories ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            tuple(cols.values()),
        )
        self._replace_evidence(conn, cur.lastrowid, d.get("evidence") or [])
        return cur.lastrowid

    def _update_decision_from_file(self, conn: sqlite3.Connection, num_id: int, d: dict) -> None:
        cols = self._decision_columns_from_file(d)
        assignments = ", ".join(f"{k}=?" for k in cols)
        conn.execute(f"UPDATE memories SET {assignments} WHERE id=?", (*cols.values(), num_id))
        self._replace_evidence(conn, num_id, d.get("evidence") or [])

    @staticmethod
    def _link_supersedes_from_file(conn: sqlite3.Connection, d: dict) -> None:
        def _id(uid: str | None) -> int | None:
            if not uid:
                return None
            r = conn.execute("SELECT id FROM memories WHERE uid=?", (uid,)).fetchone()
            return r[0] if r else None

        me = _id(d["uid"])
        conn.execute("UPDATE memories SET superseded_by=? WHERE id=?", (_id(d.get("superseded_by")), me))
        older = _id(d.get("supersedes"))
        if older is not None:
            conn.execute(
                "UPDATE memories SET superseded_by=?, "
                "valid_until=COALESCE(valid_until, ?), "
                "invalidated_reason=COALESCE(invalidated_reason, ?) WHERE id=?",
                (me, d.get("date") or datetime.now(timezone.utc).isoformat(),
                 f"superseded by {d['uid']}", older),
            )

    # ------------------------- helpers -------------------------

    @staticmethod
    def _extract_keywords(text: str) -> str:
        stop = {
            "the","a","an","is","are","was","were","be","been","have","has",
            "do","does","did","will","would","could","should","may","might",
            "this","that","with","from","for","and","or","but","in","on",
            "at","to","of","not","what",
            "how","why","when","where","which","who","can","just","also",
        }
        words = text.lower().split()
        cleaned_words = [w.strip(".,!?;:\"'()[]{}") for w in words]
        keywords = [w for w in cleaned_words
                    if w not in stop and len(w) >= 3]
        return ",".join(dict.fromkeys(keywords[:25]))
