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


def sqlite_connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


_SENTINEL = object()


class Memory:
    def __init__(self, config: dict):
        cfg = config.get("memory", {})
        self.enabled = cfg.get("enabled", True)
        self.max_results = cfg.get("max_results", 5)
        self.backend = cfg.get("backend", "sqlite")
        db_path = Path(cfg.get("db_path", "~/.cairn/memory.db")).expanduser()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path

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

        # async store
        self.async_store_enabled = cfg.get("async_store", True)
        self._write_queue: "queue.Queue" = queue.Queue()
        self._writer_thread: threading.Thread | None = None

        self._session: list[dict] = []
        self._chroma = None
        self._failed_writes = 0

        self.fts5_enabled = False

        if self.enabled:
            self._init_sqlite()
            if self.backend == "hybrid":
                self._init_chroma(db_path.parent)
            if self.async_store_enabled:
                self._start_writer()

    # ------------------------- init / lifecycle -------------------------

    def _init_sqlite(self):
        with sqlite_connect(self.db_path) as conn:
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
            self._init_fts5(conn)

    def _init_fts5(self, conn: sqlite3.Connection):
        self.fts5_enabled = False
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
            self.fts5_enabled = True
        except sqlite3.OperationalError as e:
            import sys
            print(f"FTS5 initialization warning (using legacy fallback): {e}", file=sys.stderr)

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

    def store(self, prompt: str, response: str, importance: int = 1):
        if not self.enabled:
            return
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "prompt": prompt,
            "response": response,
            "keywords": self._extract_keywords(prompt + " " + response[:1000]),
            "importance": importance,
            "content_hash": self._content_hash(prompt, response),
        }
        self._session.append(entry)
        if self.async_store_enabled:
            self._write_queue.put(entry)
        else:
            self._write_entry(entry)

    def _write_entry(self, entry: dict) -> None:
        # Dedup hook, activates if the dedup module is importable
        try:
            from cairn.memory.dedup import maybe_merge
            if maybe_merge(self, entry):
                return
        except ImportError:
            pass

        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO memories (timestamp, prompt, response, keywords, importance, "
                "content_hash, valid_from) VALUES (?,?,?,?,?,?,?)",
                (
                    entry["timestamp"],
                    entry["prompt"],
                    entry["response"][:3000],
                    entry["keywords"],
                    entry["importance"],
                    entry["content_hash"],
                    entry["timestamp"],
                ),
            )
            row_id = cursor.lastrowid
            conn.commit()
        entry["id"] = row_id
        if self._chroma:
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

    def recall(self, prompt: str, limit: int | None = None) -> str:
        if not self.enabled:
            return ""
        limit = limit or self.max_results
        results: list[dict] = []
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
                                    "WHERE id = ? AND valid_until IS NULL", (db_id,)
                                ).fetchone()
                            if row:
                                results.append({
                                    "id": db_id,
                                    "kind": row[0] or "memory",
                                    "timestamp": row[1],
                                    "prompt": row[2],
                                    "response": row[3],
                                    "content": f"Q: {row[2]}\nA: {row[3]}",
                                })
                        except (ValueError, TypeError):
                            pass
            except Exception as e:
                import sys
                print(f"Chroma query error: {e}", file=sys.stderr)

        for r in self._sqlite_keyword_search(prompt, limit=limit):
            if not any(r["id"] == x.get("id") for x in results):
                results.append({
                    "id": r["id"],
                    "kind": r["kind"],
                    "timestamp": r["timestamp"],
                    "prompt": r["prompt"],
                    "response": r["response"],
                    "content": f"Q: {r['prompt']}\nA: {r['response']}",
                })
        if not results:
            return ""
        formatted = []
        for r in results[:limit]:
            date_str = "unknown"
            if r["timestamp"]:
                date_str = r["timestamp"][:10]
            header = f"[#{r['id']} · {r['kind']} · {date_str}]"
            content = r["content"][:600]
            formatted.append(f"{header}\n{content}")
        return "\n---\n".join(formatted)

    def _sqlite_keyword_search(self, prompt: str, limit: int = 5) -> list[dict]:
        if getattr(self, "fts5_enabled", False):
            # FTS5 search path
            keywords = [w for w in self._extract_keywords(prompt).split(",") if w]
            if not keywords:
                return []
            match_query = " OR ".join(keywords)
            with sqlite_connect(self.db_path) as conn:
                try:
                    rows = conn.execute(
                        "SELECT id, prompt, response, timestamp, kind, importance, -bm25(memories_fts) AS score_bm25 "
                        "FROM memories JOIN memories_fts ON memories.id = memories_fts.rowid "
                        "WHERE memories.valid_until IS NULL AND memories_fts MATCH ? "
                        "ORDER BY score_bm25 DESC LIMIT 100",
                        (match_query,),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if rows:
                with sqlite_connect(self.db_path) as conn:
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
                        "kind": rknd or "memory"
                    }))
                scored.sort(reverse=True, key=lambda x: x[0])
                return [item for _, item in scored[:limit]]

        # Legacy keyword search fallback
        keywords = set(self._extract_keywords(prompt).split(","))
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, prompt, response, keywords, importance, timestamp, kind "
                "FROM memories WHERE valid_until IS NULL "
                "ORDER BY id DESC LIMIT 400"
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
                "kind": rknd or "memory"
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

    def search(self, query: str, limit: int = 10) -> list[dict]:
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT timestamp, prompt, response FROM memories "
                "WHERE (prompt LIKE ? OR response LIKE ?) AND valid_until IS NULL "
                "ORDER BY id DESC LIMIT ?",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
        return [{"timestamp": r[0], "prompt": r[1], "response": r[2]} for r in rows]

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

    def record_decision(
        self,
        decision: str,
        reasoning: str | None = None,
        alternatives: list[dict] | None = None,
        evidence: list[dict] | None = None,
        supersedes: int | None = None,
        chose_because: str | None = None,
        importance: int = 2,
    ) -> int:
        """Insert a decision row synchronously and return its id.

        alternatives: [{"option": str, "rejected_because": str}, ...]
        evidence:     [{"source": str, "claim": str}, ...]
        supersedes:   prior decision id; if set, that row is invalidated and
                      its superseded_by is filled.
        """
        import json as _json

        self.flush()
        now = datetime.now(timezone.utc).isoformat()
        alts_json = _json.dumps(alternatives or [], ensure_ascii=False) if alternatives else None

        with sqlite_connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO memories (timestamp, prompt, response, keywords, importance, "
                "content_hash, valid_from, kind, reasoning, alternatives_json, chose_because) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
                ),
            )
            new_id = cursor.lastrowid
            for ev in evidence or []:
                conn.execute(
                    "INSERT INTO memory_evidence (memory_id, source, claim) VALUES (?,?,?)",
                    (new_id, ev.get("source"), ev.get("claim")),
                )
            superseded_ok = False
            if supersedes is not None:
                cur = conn.execute(
                    "UPDATE memories SET valid_until=?, invalidated_reason=?, superseded_by=? "
                    "WHERE id=? AND valid_until IS NULL",
                    (now, f"superseded by #{new_id}", new_id, supersedes),
                )
                superseded_ok = cur.rowcount > 0
            conn.commit()

        if superseded_ok and self._chroma:
            try:
                self._chroma.delete(ids=[str(supersedes)])
            except Exception as e:
                import sys
                print(f"Chroma delete error for id {supersedes}: {e}", file=sys.stderr)
                self._failed_writes += 1

        self._export_decisions_to_markdown()
        return new_id

    def get_full(self, mem_id: int) -> dict | None:
        """Return a full memory row including evidence + supersedes chain pointers."""
        import json as _json

        with sqlite_connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, timestamp, prompt, response, keywords, importance, "
                "valid_from, valid_until, invalidated_reason, kind, reasoning, "
                "alternatives_json, superseded_by, chose_because "
                "FROM memories WHERE id=?",
                (mem_id,),
            ).fetchone()
            if not row:
                return None
            evidence = [
                {"source": s, "claim": c}
                for s, c in conn.execute(
                    "SELECT source, claim FROM memory_evidence WHERE memory_id=?",
                    (mem_id,),
                ).fetchall()
            ]
            supersedes_id = conn.execute(
                "SELECT id FROM memories WHERE superseded_by=?", (mem_id,)
            ).fetchone()
        out = {
            "id": row[0], "timestamp": row[1], "decision": row[2], "response": row[3],
            "keywords": row[4], "importance": row[5], "valid_from": row[6],
            "valid_until": row[7], "invalidated_reason": row[8], "kind": row[9],
            "reasoning": row[10],
            "alternatives": _json.loads(row[11]) if row[11] else [],
            "superseded_by": row[12], "chose_because": row[13],
            "supersedes": supersedes_id[0] if supersedes_id else None,
            "evidence": evidence,
        }
        return out

    def thread(self, topic_or_id: str | int, limit: int = 20) -> list[dict]:
        """Chronological list of decision versions on the same topic.

        If int: walk the supersedes chain (newest first → oldest).
        If str: keyword-overlap group across decisions.
        """
        rows: list[dict] = []
        with sqlite_connect(self.db_path) as conn:
            if isinstance(topic_or_id, int):
                seen: set[int] = set()
                current: int | None = topic_or_id
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
                        "id": r[0], "timestamp": r[1], "decision": r[2],
                        "kind": r[3], "active": r[4] is None,
                    })
                    current = r[5]
            else:
                keywords = set(self._extract_keywords(str(topic_or_id)).split(","))
                if not keywords:
                    return []
                all_rows = conn.execute(
                    "SELECT id, timestamp, prompt, keywords, kind, valid_until "
                    "FROM memories WHERE kind='decision' ORDER BY timestamp ASC LIMIT 500"
                ).fetchall()
                for rid, ts, p, kw, kind, vu in all_rows:
                    if not kw:
                        continue
                    if keywords & set(kw.split(",")):
                        rows.append({
                            "id": rid, "timestamp": ts, "decision": p,
                            "kind": kind, "active": vu is None,
                        })
                        if len(rows) >= limit:
                            break
        return rows

    def decision_audit_score(self, mem_id: int) -> dict:
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
        return {"id": mem_id, "score": score, "total": 4, "checks": checks, "missing": missing}

    # ------------------------- temporal (γ) -------------------------

    def invalidate(self, mem_id: int, reason: str = "") -> bool:
        """Invalidate a record (does not delete; sets valid_until to now)."""
        self.flush()
        now = datetime.now(timezone.utc).isoformat()
        with sqlite_connect(self.db_path) as conn:
            cur = conn.execute(
                "UPDATE memories SET valid_until=?, invalidated_reason=? "
                "WHERE id=? AND valid_until IS NULL",
                (now, reason or None, mem_id),
            )
            conn.commit()
            ok = cur.rowcount > 0
        if ok:
            if self._chroma:
                try:
                    self._chroma.delete(ids=[str(mem_id)])
                except Exception as e:
                    import sys
                    print(f"Chroma delete error for id {mem_id}: {e}", file=sys.stderr)
                    self._failed_writes += 1
            self._export_decisions_to_markdown()
        return ok

    def _export_decisions_to_markdown(self) -> None:
        cairn_dir = Path.cwd() / ".cairn"
        if not cairn_dir.exists():
            return
        persistence_dir = cairn_dir / "persistence" / "memory"
        persistence_dir.mkdir(parents=True, exist_ok=True)
        decisions_file = persistence_dir / "decisions.md"
        
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, timestamp, prompt, reasoning, alternatives_json, "
                "superseded_by, chose_because, valid_until, importance "
                "FROM memories WHERE kind='decision' ORDER BY id ASC"
            ).fetchall()
            
        import json as _json
        lines = [
            "# Decisions Log",
            "",
            "This file is an auto-generated mirror of Cairn's decision database for Git versioning.",
            "Do not edit this file manually.",
            "",
        ]
        
        for r in rows:
            mem_id, ts, decision, reasoning, alts_json, superseded_by, chose_because, valid_until, importance = r
            status = "ACTIVE"
            if valid_until:
                if superseded_by:
                    status = f"SUPERSEDED BY #{superseded_by}"
                else:
                    status = "INVALIDATED"
            
            lines.append(f"## Decision #{mem_id}, {status}")
            lines.append(f"- **Date:** {ts}")
            lines.append(f"- **Importance:** {importance}")
            lines.append(f"- **Decision:** {decision}")
            if reasoning:
                lines.append(f"- **Reasoning:** {reasoning}")
            if chose_because:
                lines.append(f"- **Chose Because:** {chose_because}")
            if valid_until:
                lines.append(f"- **Valid Until:** {valid_until}")
            if superseded_by:
                lines.append(f"- **Superseded By:** #{superseded_by}")
            
            if alts_json:
                try:
                    alts = _json.loads(alts_json)
                    if alts:
                        lines.append("- **Alternatives Considered:**")
                        for alt in alts:
                            opt = alt.get("option", "")
                            rej = alt.get("rejected_because", "")
                            lines.append(f"  - *{opt}* (rejected: {rej})")
                except Exception:
                    pass
            
            with sqlite_connect(self.db_path) as conn:
                ev_rows = conn.execute(
                    "SELECT source, claim FROM memory_evidence WHERE memory_id = ?", (mem_id,)
                ).fetchall()
            if ev_rows:
                lines.append("- **Evidence:**")
                for src, claim in ev_rows:
                    lines.append(f"  - *{src}:* {claim}")
            lines.append("")
            
        decisions_file.write_text("\n".join(lines), encoding="utf-8")

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
