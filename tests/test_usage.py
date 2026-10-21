"""Transcript measurement: possibly missed decisions and token use."""
from __future__ import annotations

import json
from pathlib import Path

from cairn.interface.i18n import STRINGS, current_language, t
from cairn.memory.bridge import claude_memory_dir
from cairn.memory.usage import billable_input, project_report, session_report


def _user(text, ts="2026-10-21T10:00:00Z", **extra):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}, **extra}


def _assistant(msg_id, tools=(), usage=None, ts="2026-10-21T10:00:05Z"):
    content = [{"type": "tool_use", "name": n, "input": {}} for n in tools]
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": msg_id, "content": content,
                        "usage": usage or {"input_tokens": 10, "cache_read_input_tokens": 100,
                                           "cache_creation_input_tokens": 20, "output_tokens": 5}}}


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


def test_signal_followed_by_record_decision_is_covered(tmp_path):
    r = session_report(_write(tmp_path / "s.jsonl", [
        _user("From now on we use SQLite"),
        _assistant("m1", ["mcp__cairn__record_decision"]),
        _user("Bundan sonra çerezleri httpOnly yapalım"),
        _assistant("m2"),
    ]))
    assert len(r["signals"]) == 2
    assert [m["signal"] for m in r["missed"]] == ["Bundan sonra"]
    assert r["recorded"] == 1 and r["cairn_calls"] == 1


def test_pasted_logs_continuations_and_meta_are_not_signals(tmp_path):
    r = session_report(_write(tmp_path / "s.jsonl", [
        _user('fix this <pasted_content id="1">we decided to migrate to v2</pasted_content>'),
        _user("This session is being continued from a previous conversation. We agreed to use SQLite."),
        _user("from now on use tabs", isMeta=True),
        _user([{"type": "tool_result", "content": "from now on"}]),
    ]))
    assert r["signals"] == []


def test_token_usage_counts_each_model_call_once(tmp_path):
    row = _assistant("same")
    r = session_report(_write(tmp_path / "s.jsonl", [_user("hi"), row, row, _assistant("other")]))
    assert r["model_calls"] == 2
    assert r["tokens"] == {"input": 20, "cache_write": 40, "cache_read": 200, "output": 10}
    assert billable_input(r["tokens"]) == 20 + 1.25 * 40 + 0.1 * 200


def test_project_report_splits_sessions_by_cairn_use(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    home = tmp_path / "claude"
    folder = claude_memory_dir(project, claude_home=home).parent
    folder.mkdir(parents=True)
    _write(folder / "a.jsonl", [_user("go"), _assistant("a1", ["mcp__cairn__memory_context"])])
    _write(folder / "b.jsonl", [_user("We agreed to use Redis"), _assistant("b1"), _assistant("b2")])
    rep = project_report(project, claude_home=home)
    assert rep["groups"]["with_cairn"]["sessions"] == 1
    assert rep["groups"]["without_cairn"]["avg_model_calls"] == 2
    assert rep["signals"] == 1 and rep["missed"] == 1 and rep["capture_rate"] == 0


def test_every_interface_string_has_every_language():
    langs = {"en", "tr", "fr", "es", "de", "zh", "ja"}
    assert all(set(v) == langs for v in STRINGS.values())


def test_language_comes_from_env_then_config(monkeypatch):
    monkeypatch.setenv("CAIRN_LANG", "tr")
    assert current_language({"ui": {"language": "de"}}) == "tr"
    monkeypatch.delenv("CAIRN_LANG")
    assert current_language({"ui": {"language": "de"}}) == "de"
    assert t("col.decision", "zh") == "决定"


def _assistant_text(text, msg_id="t1"):
    return {"type": "assistant", "timestamp": "2026-10-21T10:00:03Z",
            "message": {"id": msg_id, "content": [{"type": "text", "text": text}],
                        "usage": {"input_tokens": 1, "output_tokens": 1}}}


def test_a_yes_to_a_proposal_is_a_signal_until_it_is_recorded(tmp_path):
    proposal = _assistant_text("Redis mi Postgres mi? Önerim Redis, çünkü oturumlar kısa ömürlü. Kullanalım mı?")
    missed = session_report(_write(tmp_path / "a.jsonl", [_user("devam"), proposal, _user("Tamam, Redis ile gidelim")]))
    assert len(missed["missed"]) == 1 and "Redis" in missed["missed"][0]["quote"]
    covered = session_report(_write(tmp_path / "b.jsonl", [
        proposal, _user("Tamam, Redis ile gidelim"), _assistant("m2", ["mcp__cairn__record_decision"])]))
    assert covered["signals"] and not covered["missed"]


def test_a_yes_to_a_plain_status_message_is_not_a_signal(tmp_path):
    r = session_report(_write(tmp_path / "s.jsonl", [
        _assistant_text("Done. I fixed the typo and ran the tests."), _user("ok")]))
    assert r["signals"] == []


def test_background_task_notifications_are_not_user_messages(tmp_path):
    note = "<task-notification><task-id>x1</task-id><summary>from now on we use SQLite</summary></task-notification>"
    r = session_report(_write(tmp_path / "s.jsonl", [_user(note), _user("tamam " + note)]))
    assert r["signals"] == []
