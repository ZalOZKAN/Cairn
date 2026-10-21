"""Host probe: structure only, never message text."""
from __future__ import annotations

import json

from typer.testing import CliRunner

from cairn.interface.cli import app
from cairn.memory import probe

SECRET_TEXT = "my-secret-sentence-xyz"
CANARIES = (SECRET_TEXT, "someone@example.com", "C:/Users/private-person/work")


def _project(tmp_path):
    (tmp_path / ".cairn").mkdir()
    transcript = tmp_path / "t.jsonl"
    rows = [
        {"type": "user", "message": {"content": SECRET_TEXT}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": f"Use Redis? {CANARIES[1]}"}]}},
    ]
    transcript.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return transcript


def test_the_probe_line_has_structure_and_none_of_the_text(tmp_path, monkeypatch):
    transcript = _project(tmp_path)
    monkeypatch.chdir(tmp_path)
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": SECRET_TEXT, "cwd": str(tmp_path),
               "transcript_path": str(transcript), "note": CANARIES[2]}
    result = CliRunner().invoke(app, ["memory", "probe", "--host", "demo"], input=json.dumps(payload))
    assert result.exit_code == 0 and result.output == ""
    raw = (tmp_path / ".cairn" / "persistence" / probe.PROBE_FILE).read_text(encoding="utf-8")
    assert not any(c in raw for c in CANARIES) and str(tmp_path) not in raw
    line = json.loads(raw)
    assert line["event"] == "UserPromptSubmit" and line["prompt_present"] is True
    assert line["transcript_readable"] and line["assistant_extracted"] and line["assistant_chars"] > 0
    assert line["record_types"] == {"user": 1, "assistant": 1}
    assert line["keys"]["prompt"] == "str"


def test_a_payload_without_a_transcript_is_recorded_as_such(tmp_path):
    (tmp_path / ".cairn").mkdir()
    probe.record(tmp_path, {"event": "BeforeAgent", "prompt": "hi"}, "gemini")
    row = probe.report(tmp_path)[0]
    assert row["host"] == "gemini" and row["transcript_rate"] == 0 and row["prompt_rate"] == 1
    assert row["events"] == ["BeforeAgent"]


def test_bad_input_never_breaks_the_host(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for bad in ("", "not json", "[1, 2]"):
        result = CliRunner().invoke(app, ["memory", "probe", "--host", "x"], input=bad)
        assert result.exit_code == 0 and result.output == ""


def test_no_project_means_nothing_is_written(tmp_path):
    probe.record(tmp_path, {"prompt": "hi"}, "x")
    assert not (tmp_path / ".cairn").exists()
