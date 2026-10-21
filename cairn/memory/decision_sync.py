"""Git-native decision files (`.cairn/decisions/*.md`): export, two-way
sync, ADR import, reindex and staleness against git history. Mixed into
`Memory`."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from cairn.memory import decision_files
from cairn.memory.decision_files import DECISIONS_DIRNAME, new_uid
from cairn.security import decision_guard
from cairn.memory.storage import forbid_to_json, sqlite_connect


class DecisionFilesMixin:
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

    def _decision_as_file_dict(self, conn: sqlite3.Connection, num_id: int) -> dict | None:
        import json as _json
        row = conn.execute(
            "SELECT uid, timestamp, prompt, reasoning, alternatives_json, chose_because, "
            "importance, scope_path, valid_from, valid_until, invalidated_reason, superseded_by, anchor_commit, "
            "recorded_by, forbid_json FROM memories WHERE id=? AND kind='decision'",
            (num_id,),
        ).fetchone()
        if not row:
            return None
        (uid, ts, decision, reasoning, alts, chose, imp, path, valid_from, valid_until,
         inv_reason, superseded_by, anchor, recorded_by, forbid_json) = row
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
            "path": path, "valid_from": valid_from, "supersedes": prev[0] if prev else None,
            "superseded_by": sup_by_uid,
            "valid_until": valid_until,
            "invalidated_reason": None if superseded_by is not None else inv_reason,
            "anchor_commit": anchor,
            "recorded_by": recorded_by,
            "forbid": _json.loads(forbid_json) if forbid_json else [],
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

    def sync_decision_files(self, refresh_agents_md: bool = True) -> dict:
        """Two-way sync between `.cairn/decisions/*.md` and the project DB.

        Files win: a file whose content differs from the DB (hand edit, or a
        teammate's decision pulled via git) is imported. DB decisions with no
        file (new records, pre-files databases) are exported. Files are never
        deleted; to retire a decision, invalidate or supersede it."""
        import sys
        counts = {"imported": 0, "updated": 0, "exported": 0, "errors": 0, "quarantined": 0}
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

        touched_ids: set[int] = set()
        to_scan: list[tuple[int, dict]] = []
        with sqlite_connect(self.db_path) as conn:
            for d in parsed:
                row = conn.execute("SELECT id FROM memories WHERE uid=?", (d["uid"],)).fetchone()
                if row is None:
                    new_id = self._insert_decision_from_file(conn, d)
                    touched_ids.add(new_id)
                    to_scan.append((new_id, d))
                    counts["imported"] += 1
                    continue
                current = self._decision_as_file_dict(conn, row[0]) or {}
                if decision_files.render(current) != decision_files.render(d):
                    self._update_decision_from_file(conn, row[0], d)
                    counts["updated"] += 1
                    touched_ids.add(row[0])
                    to_scan.append((row[0], d))
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
        # A file edited by hand or pulled from a teammate is imported as-is,
        # so it gets the same injection scan as record_decision; a flagged
        # one goes inactive and its file is rewritten to say so.
        for rid, d in to_scan:
            if d.get("status", "active") != "active":
                continue
            flags = decision_guard.scan(d.get("decision", ""), d.get("reasoning"), decision_guard.decision_texts(d))
            if flags:
                self.quarantine(rid, flags)
                self._export_decision(rid)
                counts["quarantined"] += 1
        for rid in missing:
            if self._export_decision(rid):
                counts["exported"] += 1
        if refresh_agents_md:
            # imported/updated rows are pulled from committed files, usually
            # alongside an already-current AGENTS.md; excluding them from the
            # "missing" check only matters when that assumption doesn't hold
            self._refresh_agents_md(exclude_ids=touched_ids)
        self.last_sync = counts
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
            "recorded_by": d.get("recorded_by"),
            "forbid_json": forbid_to_json(d.get("forbid"), strict=False),
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
        # Only write when the file states it: every file gets re-linked here in
        # glob order, and an unreexported older file is silent on this field,
        # so writing NULL unconditionally could erase a link the newer file's
        # `supersedes` branch just set, depending on processing order.
        if d.get("superseded_by"):
            conn.execute("UPDATE memories SET superseded_by=? WHERE id=?", (_id(d["superseded_by"]), me))
        older = _id(d.get("supersedes"))
        if older is not None:
            conn.execute(
                "UPDATE memories SET superseded_by=?, "
                "valid_until=COALESCE(valid_until, ?), "
                "invalidated_reason=COALESCE(invalidated_reason, ?) WHERE id=?",
                (me, d.get("date") or datetime.now(timezone.utc).isoformat(),
                 f"superseded by {d['uid']}", older),
            )
