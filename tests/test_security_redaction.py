"""Secret redaction before write, Competitive Response Plan Step 2."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from cairn.config.loader import load_config
from cairn.memory.memory import Memory
from cairn.security.guard import mask_secrets


def _mk_memory(tmpdir: str, **overrides) -> Memory:
    config = load_config()
    config["memory"]["db_path"] = f"{tmpdir}/test.db"
    config["memory"]["global_db_path"] = f"{tmpdir}/global.db"
    config["memory"]["async_store"] = False
    config["memory"].setdefault("dedup", {})["enabled"] = overrides.pop("dedup", False)
    config["memory"]["redact"] = {
        "enabled": overrides.pop("redact_enabled", True),
        "extra_patterns": overrides.pop("extra_patterns", []),
    }
    return Memory(config, project_dir=Path(tmpdir))


# --- module-level mask_secrets ---------------------------------------------

def test_mask_secrets_anthropic_key():
    out = mask_secrets("my key is sk-ant-abcdefghijklmnopqrstuvwx")
    assert "sk-ant-abcdefghijklmnopqrstuvwx" not in out
    assert "sk-ant-***" in out


def test_mask_secrets_github_token():
    out = mask_secrets("ghp_abcdefghijklmnopqrstuvwxyz1234567890")
    assert "abcdefghijklmnopqrstuvwxyz1234567890" not in out


def test_mask_secrets_aws_key():
    out = mask_secrets("AKIAABCDEFGHIJKLMNOP")
    assert out == "AKIA***"


def test_mask_secrets_slack_token():
    out = mask_secrets("xoxb-1234567890-abcdefghij")
    assert "1234567890-abcdefghij" not in out


def test_mask_secrets_pem_block():
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\nsecretbody\n-----END RSA PRIVATE KEY-----"
    out = mask_secrets(pem)
    assert "secretbody" not in out
    assert "MIIEpAIBAAKCAQEA" not in out


def test_mask_secrets_generic_password_field():
    out = mask_secrets("password=SuperSecretValue123")
    assert "SuperSecretValue123" not in out


def test_mask_secrets_leaves_normal_text_alone():
    text = "we decided to use httpOnly cookies for jwt storage"
    assert mask_secrets(text) == text


def test_mask_secrets_extra_patterns():
    out = mask_secrets("internal-id: PROJ-99999", extra_patterns=[(r"PROJ-\d+", "PROJ-***")])
    assert "PROJ-99999" not in out


# --- wired into Memory.store / record_decision ------------------------------

def test_store_redacts_before_write():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        mem.store("deploy key", "use sk-ant-abcdefghijklmnopqrstuvwx for the CI job")
        with sqlite3.connect(mem.db_path) as conn:
            response = conn.execute("SELECT response FROM memories LIMIT 1").fetchone()[0]
        assert "sk-ant-abcdefghijklmnopqrstuvwx" not in response
        assert "sk-ant-***" in response


def test_store_redaction_disabled_keeps_raw_text():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir, redact_enabled=False)
        mem.store("deploy key", "use sk-ant-abcdefghijklmnopqrstuvwx for the CI job")
        with sqlite3.connect(mem.db_path) as conn:
            response = conn.execute("SELECT response FROM memories LIMIT 1").fetchone()[0]
        assert "sk-ant-abcdefghijklmnopqrstuvwx" in response


def test_record_decision_redacts_reasoning_and_alternatives():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        new_id = mem.record_decision(
            "rotate the API key",
            reasoning="the old one AKIAABCDEFGHIJKLMNOP was committed by mistake",
            alternatives=[{"option": "keep using ghp_abcdefghijklmnopqrstuvwxyz1234567890",
                            "rejected_because": "already leaked"}],
        )
        full = mem.get_full(new_id)
        assert "AKIAABCDEFGHIJKLMNOP" not in full["reasoning"]
        assert "ghp_abcdefghijklmnopqrstuvwxyz1234567890" not in full["alternatives"][0]["option"]


def test_store_redacts_across_global_scope_too():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        mem = _mk_memory(tmpdir)
        mem.store("shared secret", "team key sk-ant-abcdefghijklmnopqrstuvwx", scope="global")
        with sqlite3.connect(mem.global_db_path) as conn:
            response = conn.execute("SELECT response FROM memories LIMIT 1").fetchone()[0]
        assert "sk-ant-abcdefghijklmnopqrstuvwx" not in response
