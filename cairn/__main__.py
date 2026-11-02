"""Run Cairn as `python -m cairn`, for machines that block the unsigned cairn.exe launcher pip creates."""
from __future__ import annotations

from cairn.interface.cli import main

if __name__ == "__main__":
    main()
