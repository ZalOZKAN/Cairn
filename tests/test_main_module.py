"""`python -m cairn` must work without the generated cairn.exe launcher."""
from __future__ import annotations

import os
import subprocess
import sys


def test_python_dash_m_cairn_prints_help():
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run([sys.executable, "-m", "cairn", "--help"], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", env=env, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "Usage" in result.stdout
