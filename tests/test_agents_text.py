"""AGENTS.md text and CLI help in the project's language."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cairn.interface.cli import app
from cairn.interface.help_i18n import HELP
from cairn.memory import agents_md, agents_text
from cairn.memory.memory import Memory


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        r = Path(d)
        (r / ".cairn").mkdir()
        yield r


def _mem(root: Path, lang: str | None = None) -> Memory:
    cfg = {"memory": {"async_store": False, "global_enabled": False}}
    if lang:
        cfg["memory"]["agents_md"] = {"language": lang}
    return Memory(cfg, project_dir=root)


def test_every_language_has_every_text_key():
    keys = set(agents_text.TEXT["en"])
    assert all(set(t) == keys for t in agents_text.TEXT.values())


@pytest.mark.parametrize("lang", sorted(agents_text.TEXT))
def test_block_is_written_in_the_project_language_and_reads_back(root, lang):
    m = _mem(root, lang)
    m.record_decision("Use SQLite for storage", reasoning="no server",
                      alternatives=[{"option": "Postgres", "rejected_because": "needs a server"}])
    m.record_decision("Use cookies", scope_path="web")
    body = agents_md.body(m)
    tx = agents_text.text(lang)
    assert tx["heading"] in body and tx["follow"] in body and f"### {tx['repo_wide']}" in body
    parsed = agents_md._parse_body(body)
    entry = parsed[None]["Use SQLite for storage"]
    assert entry["reasoning"] == "no server"
    assert entry["alternatives"] == [{"option": "Postgres", "rejected_because": "needs a server"}]
    assert "Use cookies" in parsed["web"]


def test_switching_language_does_not_misread_the_old_block(root):
    m = _mem(root, "tr")
    m.record_decision("Use SQLite for storage", reasoning="no server")
    turkish = agents_md.body(m)
    # a block written in Turkish still parses when the project is now English
    assert agents_md._parse_body(turkish)[None]["Use SQLite for storage"]["reasoning"] == "no server"


def test_language_is_a_project_setting_not_the_locale(monkeypatch):
    monkeypatch.setenv("CAIRN_LANG", "de")
    assert agents_text.language({"memory": {}}) == "en"
    assert agents_text.language({"ui": {"language": "ja"}}) == "ja"
    assert agents_text.language({"memory": {"agents_md": {"language": "zh"}}, "ui": {"language": "ja"}}) == "zh"


def test_every_command_has_translated_help():
    paths = {""}

    def walk(t, prefix):
        for c in t.registered_commands:
            paths.add(prefix + (c.name or c.callback.__name__.replace("_", "-")))
        for g in t.registered_groups:
            paths.add(prefix + g.name)
            walk(g.typer_instance, prefix + g.name + " ")

    walk(app, "")
    assert paths <= set(HELP), sorted(paths - set(HELP))


def test_set_language_edits_config_in_place_and_keeps_comments(tmp_path):
    from cairn.interface.commands.init import PROFILE_TEMPLATES
    from cairn.interface.commands.project import set_language
    import yaml
    cfg = tmp_path / "config.yaml"
    cfg.write_text(PROFILE_TEMPLATES["none"].replace("__LANG__", "en"), encoding="utf-8")
    set_language(cfg, ui="tr", agents="zh")
    text = cfg.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert data["ui"]["language"] == "tr" and data["memory"]["agents_md"]["language"] == "zh"
    assert "# keep every active decision" in text


def test_set_language_adds_missing_sections(tmp_path):
    from cairn.interface.commands.project import set_language
    import yaml
    cfg = tmp_path / "config.yaml"
    cfg.write_text("memory:\n  async_store: false\n", encoding="utf-8")
    set_language(cfg, ui="de", agents="de")
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert data["ui"]["language"] == "de"
    assert data["memory"] == {"agents_md": {"language": "de"}, "async_store": False}


def test_lang_command_rewrites_agents_md(root, monkeypatch):
    from typer.testing import CliRunner
    monkeypatch.chdir(root)
    (root / ".cairn" / "config.yaml").write_text("memory: {async_store: false, global_enabled: false}\n",
                                                 encoding="utf-8")
    (root / "AGENTS.md").write_text("<!-- cairn:begin -->\n<!-- cairn:end -->\n", encoding="utf-8")
    _mem(root).record_decision("Use SQLite for storage")
    out = CliRunner().invoke(app, ["lang", "tr"])
    assert out.exit_code == 0, out.output
    assert "Proje kararları (Cairn)" in (root / "AGENTS.md").read_text(encoding="utf-8")


def test_set_language_never_duplicates_a_one_line_memory_key(tmp_path):
    from cairn.interface.commands.project import set_language
    import yaml
    cfg = tmp_path / "config.yaml"
    cfg.write_text("memory: {async_store: false}\n", encoding="utf-8")
    set_language(cfg, ui="fr", agents="fr")
    text = cfg.read_text(encoding="utf-8")
    assert text.count("memory:") == 1
    data = yaml.safe_load(text)
    assert data["memory"] == {"async_store": False} and data["ui"]["language"] == "fr"
    assert agents_text.language(data) == "fr"
    with pytest.raises(ValueError):
        set_language(cfg, agents="de")
