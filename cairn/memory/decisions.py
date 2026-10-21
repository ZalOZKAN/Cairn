"""Decision records: recording, provenance, candidates, threads, temporal
validity and path lookups. Mixed into `Memory`; relies on its storage
helpers (`self.db_path`, `_normalize_path`, `_content_hash`, ...)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from cairn.memory.decay import decayed_score
from cairn.memory.decision_files import new_uid
from cairn.security import decision_guard
from cairn.memory.storage import AUTO_FULL_CHARS, _UID_RE, _as_utc, forbid_to_json, sqlite_connect


class DecisionsMixin:
    # ------------------------- decision provenance -------------------------

    def _resolve_scope_id(self, mem_id: int | str) -> tuple[Path, int]:
        """Map a public id ("g<n>" or plain int) to (db_path, raw int id)."""
        s = str(mem_id).strip()
        if _UID_RE.fullmatch(s):
            dbs = [self.db_path] + ([self.global_db_path] if self.global_enabled else [])
            for db in dbs:
                with sqlite_connect(db) as conn:
                    row = conn.execute("SELECT id FROM memories WHERE uid=?", (s,)).fetchone()
                if row:
                    return db, row[0]
            raise ValueError(f"no decision with uid {s}")
        if s.startswith("g") and s[1:].isdigit():
            if not self.global_enabled:
                raise ValueError("global memory is disabled (config: memory.global_enabled)")
            return self.global_db_path, int(s[1:])
        return self.db_path, int(mem_id)

    def check_conflicts(self, prompt: str, response: str) -> list[dict]:
        """Opt-in (`memory.conflict.enabled`, default false): ask the helper LLM
        whether `prompt`/`response` contradicts a prior high-importance record
        (project or global). Returns `[{"id", "reason"}, ...]`, empty if the
        feature is off or nothing conflicts. Never invalidates anything itself;
        see `cairn/memory/conflict.py` for the false-positive rationale."""
        from cairn.memory.conflict import ConflictDetector
        detector = ConflictDetector(self._config)
        return detector.detect(self, prompt, response)

    def duplicate_candidates(
        self,
        decision: str,
        reasoning: str | None = None,
        scope_path: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """Return deterministic, propose-only duplicate/related candidates."""
        from cairn.memory.dedup import candidate_relation

        target = f"{decision} {reasoning or ''}"
        norm_scope = self._normalize_path(scope_path) if scope_path else None
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, prompt, reasoning, scope_path, content_hash "
                "FROM memories WHERE kind='decision' AND valid_until IS NULL "
                "AND ((scope_path IS NULL AND ? IS NULL) OR scope_path = ?) "
                "ORDER BY id DESC LIMIT 200",
                (norm_scope, norm_scope),
            ).fetchall()
        out = []
        target_hash = self._content_hash(decision, reasoning or "")
        for rid, prompt, prior_reasoning, path, content_hash in rows:
            if content_hash == target_hash:
                relation, score = "exact-duplicate", 1.0
            else:
                relation, score = candidate_relation(
                    target, f"{prompt} {prior_reasoning or ''}"
                )
            if relation != "none":
                out.append({
                    "id": rid, "decision": prompt, "scope_path": path,
                    "relation": relation, "score": round(score, 3),
                })
        out.sort(key=lambda row: (-row["score"], -row["id"]))
        return out[:limit]

    def conflict_candidates(
        self,
        decision: str,
        reasoning: str | None = None,
        scope_path: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """Return deterministic conflict warnings; never changes stored state."""
        import json

        from cairn.memory.dedup import conflict_relation

        target = f"{decision} {reasoning or ''}"
        norm_scope = self._normalize_path(scope_path) if scope_path else None
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, prompt, reasoning, alternatives_json, scope_path "
                "FROM memories WHERE kind='decision' AND valid_until IS NULL "
                "AND ((scope_path IS NULL AND ? IS NULL) OR scope_path = ?) "
                "ORDER BY id DESC LIMIT 200",
                (norm_scope, norm_scope),
            ).fetchall()
        out = []
        for rid, prompt, prior_reasoning, alternatives_json, path in rows:
            rejected = [str(item.get("option", "")) for item in json.loads(alternatives_json or "[]")
                        if isinstance(item, dict)]
            relation, overlap = conflict_relation(target, f"{prompt} {prior_reasoning or ''}", rejected)
            if relation != "none":
                out.append({
                    "id": rid, "decision": prompt, "scope_path": path,
                    "relation": relation, "overlap": round(overlap, 3),
                })
        out.sort(key=lambda row: (-row["overlap"], -row["id"]))
        return out[:limit]

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
        recorded_by: str | None = None,
        forbid: list[str] | None = None,
        valid_from: str | None = None,
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
        recorded_by:  the tool that recorded it (MCP client name, "cli"), so
                      a project worked on from several tools shows where
                      each decision came from.
        forbid:       regexes that code added under this decision's scope must
                      not match (`cairn memory guard`); invalid ones raise
                      ValueError before anything is written.
        """
        import json as _json

        forbid_json = forbid_to_json(forbid)
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
        for ev in evidence or []:
            ref = self._evidence_ref(ev["source"])
            if ref and not self._ref_head(ref):
                raise ValueError(f"evidence cites {ref}, which does not exist")

        # Active decisions are read by every agent as instructions; one that
        # looks like an injection is stored inactive until a human approves it
        # (see cairn/security/decision_guard.py). It must not retire another.
        flags = decision_guard.scan(
            decision, reasoning, decision_guard.decision_texts({"alternatives": alternatives, "evidence": evidence}))
        quarantine = decision_guard.QUARANTINE_PREFIX + "; ".join(flags) if flags else None
        if quarantine and supersedes is not None:
            quarantine += f" (would supersede #{supersedes})"
            supersedes = None

        self.flush()
        now = datetime.now(timezone.utc).isoformat()
        effective_from = now
        if valid_from:
            try:
                effective_from = _as_utc(valid_from).isoformat()
            except ValueError as exc:
                raise ValueError("valid_from must be an ISO-8601 timestamp") from exc
            if _as_utc(effective_from) > _as_utc(now):
                raise ValueError("valid_from cannot be in the future")
        alts_json = _json.dumps(alternatives or [], ensure_ascii=False) if alternatives else None
        norm_scope = self._normalize_path(scope_path) if scope_path else None

        with sqlite_connect(target_db) as conn:
            cursor = conn.execute(
                "INSERT INTO memories (timestamp, prompt, response, keywords, importance, "
                "content_hash, valid_from, kind, reasoning, alternatives_json, chose_because, scope_path, "
                "uid, anchor_commit, recorded_by, forbid_json, valid_until, invalidated_reason) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    now,
                    decision,
                    reasoning or "",
                    self._extract_keywords(decision + " " + (reasoning or "")),
                    importance,
                    self._content_hash(decision, reasoning or ""),
                    effective_from,
                    "decision",
                    reasoning,
                    alts_json,
                    chose_because,
                    norm_scope,
                    new_uid(datetime.fromisoformat(now)),
                    None if is_global else self._git_head(),
                    recorded_by,
                    forbid_json,
                    now if quarantine else None,
                    quarantine,
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
                        (effective_from, f"superseded by #{supersede_marker}", new_id, sup_num_id),
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
                    (effective_from, f"superseded by #{supersede_marker}", new_id, sup_num_id),
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
        if not is_global or superseded_ok:
            # new_id (now active) and, on a same-scope supersede, sup_num_id
            # (now inactive) both just changed status in this exact call; the
            # file on disk is always one regeneration behind at this point
            # (that regeneration is what this call is about to do), so
            # neither must be mistaken for a hand edit on either side of the diff
            exclude = ({new_id} if not is_global else set())
            if superseded_ok and sup_db == self.db_path:
                exclude.add(sup_num_id)
            self._refresh_agents_md(exclude_ids=exclude or None)
        return f"g{new_id}" if is_global else new_id

    @staticmethod
    def _evidence_ref(source: str | None) -> str | None:
        """The id in an evidence source written as `ref:<id|g<n>|uid>`, else None."""
        s = (source or "").strip()
        return s[4:].strip() or None if s.lower().startswith("ref:") else None

    def _ref_head(self, ref: str) -> dict | None:
        """One-line view of a cited entry: its id, text and whether it is still valid."""
        try:
            db_path, num_id = self._resolve_scope_id(ref)
        except ValueError:
            return None
        with sqlite_connect(db_path) as conn:
            row = conn.execute(
                "SELECT prompt, valid_until, invalidated_reason FROM memories WHERE id=?", (num_id,)
            ).fetchone()
        if not row:
            return None
        status = "active" if row[1] is None else f"invalidated: {row[2] or 'no reason recorded'}"
        return {"id": f"g{num_id}" if db_path == self.global_db_path else num_id, "text": row[0], "status": status}

    def get_full(self, mem_id: int | str) -> dict | None:
        """Return a full memory row including evidence + supersedes chain pointers."""
        import json as _json

        db_path, num_id = self._resolve_scope_id(mem_id)
        is_global = db_path == self.global_db_path

        with sqlite_connect(db_path) as conn:
            row = conn.execute(
                "SELECT id, timestamp, prompt, response, keywords, importance, "
                "valid_from, valid_until, invalidated_reason, kind, reasoning, "
                "alternatives_json, superseded_by, chose_because, scope_path, "
                "uid, recorded_by, forbid_json "
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
            "scope_path": row[14], "uid": row[15], "recorded_by": row[16],
            "forbid": _json.loads(row[17]) if row[17] else [],
            "scope": "global" if is_global else "project",
            "supersedes": _tag(supersedes_id[0]) if supersedes_id else None,
            "evidence": [
                {**e, "cited": self._ref_head(ref)} if (ref := self._evidence_ref(e["source"])) else e
                for e in evidence
            ],
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

        approach_kw = self._stems(approach)
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
                        opt_kw = self._stems(option)
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
                    own_kw = self._stems(prompt)
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

    @staticmethod
    def _meaningful_overlap(kw_a: set[str], kw_b: set[str]) -> int:
        """Shared-stem count, but only counted when it is a real share of
        meaning: >=2 stems AND at least half of the *shorter* decision's own
        stems, not just a couple of words (e.g. "use", "http") that happen to
        recur across two unrelated decisions filed under the same busy scope.
        Returns 0 when the pair does not qualify."""
        if not kw_a or not kw_b:
            return 0
        overlap = len(kw_a & kw_b)
        if overlap >= 2 and overlap / min(len(kw_a), len(kw_b)) >= 0.5:
            return overlap
        return 0

    def possible_supersedes(self, decision: str, scope_path: str | None, exclude_id: int | None = None) -> list[dict]:
        """Active project decisions in the same scope as `decision` that share
        a meaningful share of their keywords with it (see `_meaningful_overlap`).
        Advisory only, like `check_conflicts`: never invalidates anything: the
        caller decides whether to pass one back as `supersedes`, or invalidate
        it directly with a reason."""
        if not scope_path:
            return []
        norm_scope = self._normalize_path(scope_path)
        kw = self._stems(decision)
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, uid, prompt FROM memories WHERE kind='decision' "
                "AND valid_until IS NULL AND scope_path=?", (norm_scope,),
            ).fetchall()
        out = []
        for rid, uid, prompt in rows:
            if rid == exclude_id:
                continue
            overlap = self._meaningful_overlap(kw, self._stems(prompt))
            if overlap:
                out.append({"id": rid, "uid": uid, "decision": prompt, "overlap": overlap})
        out.sort(key=lambda r: -r["overlap"])
        return out[:3]

    def resolve_replaces(self, text: str) -> dict:
        """Find the single active project decision `text` names, by substring
        either way or a meaningful stem overlap (see `_meaningful_overlap`), so
        a caller can pass its id as `supersedes` without looking it up first:
        `{"id", "candidates"}`. `id` is set only when exactly one decision
        matches; otherwise every candidate is listed (possibly empty) and
        nothing is resolved, since a wrong guess would retire the wrong
        decision."""
        needle = " ".join(text.split()).lower()
        kw = self._stems(text)
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, uid, prompt, scope_path FROM memories "
                "WHERE kind='decision' AND valid_until IS NULL"
            ).fetchall()
        out = []
        for rid, uid, prompt, sp in rows:
            hay = prompt.strip().lower()
            overlap = self._meaningful_overlap(kw, self._stems(prompt))
            if (needle and (needle in hay or hay in needle)) or overlap:
                out.append({"id": rid, "uid": uid, "decision": prompt, "scope_path": sp, "overlap": overlap})
        out.sort(key=lambda r: -r["overlap"])
        return {"id": out[0]["id"] if len(out) == 1 else None, "candidates": out[:5]}

    def thread(self, topic_or_id: str | int, limit: int = 20, as_of: str | None = None) -> list[dict]:
        """Chronological list of decision versions on the same topic.

        If int, or a "g<id>" string: walk the supersedes chain from the
        requested decision toward newer decisions (oldest to newest).
        If a plain string: keyword-overlap group across project decisions,
        plus global decisions when global memory is enabled.
        """
        rows: list[dict] = []
        as_of_dt = None
        as_utc = _as_utc
        if as_of:
            try:
                as_of_dt = as_utc(as_of)
            except ValueError as exc:
                raise ValueError("as_of must be an ISO-8601 timestamp") from exc
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
                        "SELECT id, timestamp, prompt, kind, valid_from, valid_until, superseded_by "
                        "FROM memories WHERE id=?",
                        (current,),
                    ).fetchone()
                    if not r:
                        break
                    valid_from, valid_until = r[4], r[5]
                    in_window = not as_of_dt or (
                        as_utc(valid_from or r[1]) <= as_of_dt
                        and (not valid_until or as_utc(valid_until) > as_of_dt)
                    )
                    if in_window:
                        rows.append({
                            "id": f"g{r[0]}" if is_global else r[0], "timestamp": r[1], "decision": r[2],
                            "kind": r[3], "active": r[5] is None, "scope": "global" if is_global else "project",
                        })
                    current = r[6]
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
                    "SELECT id, timestamp, prompt, keywords, kind, valid_from, valid_until "
                    "FROM memories WHERE kind='decision' ORDER BY timestamp ASC LIMIT 500"
                ).fetchall()
            for rid, ts, p, kw, kind, vf, vu in all_rows:
                if not kw:
                    continue
                if as_of_dt:
                    start = as_utc(vf or ts)
                    end = as_utc(vu) if vu else None
                    if start > as_of_dt or (end and end <= as_of_dt):
                        continue
                if keywords & set(kw.split(",")):
                    rows.append({
                        "id": f"g{rid}" if is_global else rid, "timestamp": ts, "decision": p,
                        "kind": kind, "active": vu is None, "scope": "global" if is_global else "project",
                    })
        rows.sort(key=lambda r: (as_utc(r["timestamp"]), str(r["id"])))
        return rows[:limit]

    def decision_audit_score(self, mem_id: int | str) -> dict:
        """Return completeness score for a decision: 0-4 of {reasoning, alternatives, evidence, supersedes}."""
        full = self.get_full(mem_id)
        if not full:
            return {"id": mem_id, "score": 0, "total": 4, "missing": ["not_found"]}
        checks = {
            "has_reasoning": bool(full.get("reasoning")),
            "has_alternatives": bool(full.get("alternatives")),
            "has_evidence": bool(full.get("evidence")),
            "has_supersedes": full.get("supersedes") is not None,
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
                # num_id just went inactive in this exact call; the file on
                # disk still shows its old bullet until this regeneration,
                # which must not be mistaken for a hand-added decision
                self._refresh_agents_md(exclude_ids={num_id})
        return ok

    # ------------------------- quarantine -------------------------

    def quarantine_reason(self, mem_id: int | str) -> str | None:
        """Why a decision is quarantined, or None when it is not."""
        db_path, num_id = self._resolve_scope_id(mem_id)
        with sqlite_connect(db_path) as conn:
            row = conn.execute("SELECT invalidated_reason FROM memories WHERE id=?", (num_id,)).fetchone()
        reason = row[0] if row else None
        if reason and reason.startswith(decision_guard.QUARANTINE_PREFIX):
            return reason[len(decision_guard.QUARANTINE_PREFIX):]
        return None

    def quarantined_decisions(self) -> list[dict]:
        """Project decisions waiting for a human to approve them."""
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, uid, prompt, reasoning, invalidated_reason, recorded_by FROM memories "
                "WHERE kind='decision' AND invalidated_reason LIKE ? ORDER BY id",
                (decision_guard.QUARANTINE_PREFIX + "%",),
            ).fetchall()
        return [{"id": i, "uid": u, "decision": d, "reasoning": r,
                 "reason": q[len(decision_guard.QUARANTINE_PREFIX):], "recorded_by": by}
                for i, u, d, r, q, by in rows]

    def quarantine(self, num_id: int, flags: list[str]) -> None:
        """Make an active project decision inactive pending review."""
        with sqlite_connect(self.db_path) as conn:
            conn.execute(
                "UPDATE memories SET valid_until=?, invalidated_reason=?, superseded_by=NULL WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), decision_guard.QUARANTINE_PREFIX + "; ".join(flags), num_id),
            )
            conn.commit()

    def approve_quarantined(self, mem_id: int | str) -> bool:
        """Release a quarantined decision. Meant for a human (CLI); no MCP
        tool calls this, so an agent cannot approve its own text."""
        if self.quarantine_reason(mem_id) is None:
            return False
        db_path, num_id = self._resolve_scope_id(mem_id)
        with sqlite_connect(db_path) as conn:
            conn.execute("UPDATE memories SET valid_until=NULL, invalidated_reason=NULL WHERE id=?", (num_id,))
            conn.commit()
        if db_path == self.db_path:
            self._export_decision(num_id)
            self._refresh_agents_md(exclude_ids={num_id})
        return True

    def _refresh_agents_md(self, exclude_ids: set[int] | None = None) -> None:
        """Keep the AGENTS.md block in step with the decisions. Only an existing
        block is refreshed; `cairn init` / `cairn sync` are what add it.
        `exclude_ids`: rows whose active/inactive status the caller just
        changed, in this same call: the file on disk (not yet regenerated)
        always lags one of these by construction, so neither side of the
        two-way diff must mistake that lag for a hand edit."""
        if self._agents_md_syncing:
            return
        if self.agents_md_enabled and self.decisions_dir() is not None:
            from cairn.memory import agents_md
            agents_md.write(self, exclude_ids=exclude_ids)

    def _relative_to_project(self, path: str) -> str:
        p = Path(path)
        if p.is_absolute():
            try:
                p = p.resolve().relative_to(self.project_dir.resolve())
            except ValueError:
                return self._normalize_path(path)
        return self._normalize_path(str(p))

    def decisions_for_path(self, path: str) -> list[dict]:
        """Every active project decision scoped to `path` or one of its
        ancestor directories, most important first. Never truncated: an
        applicable decision must not be dropped. Repo-wide decisions are left
        out on purpose; they are in AGENTS.md and the session context already."""
        rel = self._relative_to_project(path)
        if not rel:
            return []
        path_sql, params = self._path_filter_sql(rel)
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, uid, prompt, reasoning, scope_path, importance FROM memories "
                "WHERE kind='decision' AND valid_until IS NULL AND scope_path IS NOT NULL"
                + path_sql + " ORDER BY importance DESC, id DESC",
                params,
            ).fetchall()
        return [
            {"id": rid, "uid": uid, "decision": d, "reasoning": r, "path": sp,
             "importance": imp, "stale": self.staleness(rid)}
            for rid, uid, d, r, sp, imp in rows
        ]

    def forbidden_for_path(self, path: str) -> list[dict]:
        """Active project decisions carrying `forbid` patterns that apply to
        `path`: repo-wide ones and ones scoped to it or an ancestor directory."""
        import json as _json
        rel = self._relative_to_project(path)
        if not rel:
            return []
        path_sql, params = self._path_filter_sql(rel)
        with sqlite_connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, prompt, reasoning, scope_path, forbid_json FROM memories "
                "WHERE kind='decision' AND valid_until IS NULL AND forbid_json IS NOT NULL"
                + path_sql + " ORDER BY importance DESC, id DESC",
                params,
            ).fetchall()
        return [{"id": rid, "decision": d, "reasoning": r, "path": sp, "forbid": _json.loads(f)}
                for rid, d, r, sp, f in rows]

    def decisions_block(self, detail: bool | str = True) -> str:
        """Render active decisions for AGENTS.md or session context.

        ``detail`` is True (every entry in full), False (index lines) or
        "auto": full while the block is short, index once it is long.
        Fetching an index line costs a tool call, so a short block is
        better sent whole.
        """
        from cairn.memory import agents_md
        if detail == "auto":
            full = agents_md.body(self, detail=True)
            return full if len(full) <= AUTO_FULL_CHARS else agents_md.body(self, detail=False)
        return agents_md.body(self, detail=bool(detail))

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
