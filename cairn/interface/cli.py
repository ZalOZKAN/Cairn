"""Cairn CLI: memory file format, no LLM runtime."""
import sys

import typer
from rich.console import Console

from cairn.interface.commands.memory import memory_app
from cairn.interface.commands.init import init_cmd
from cairn.interface.commands.mcp import mcp_app
from cairn.interface.commands.project import check_cmd, decide_cmd, doctor_cmd, handoff_cmd, sync_cmd
from cairn.interface.commands.version import version_cmd

app = typer.Typer(
    name="cairn",
    help="Cairn: portable .cairn/ folder for any AI coding agent",
    no_args_is_help=True,
)
console = Console()

app.add_typer(memory_app, name="memory")
app.add_typer(mcp_app, name="mcp")
app.command("init")(init_cmd)
app.command("sync")(sync_cmd)
app.command("check")(check_cmd)
app.command("decide")(decide_cmd)
app.command("handoff")(handoff_cmd)
app.command("doctor")(doctor_cmd)
app.command("version")(version_cmd)


def main():
    # Windows consoles default to cp1252 unless PYTHONIOENCODING is set; any
    # command that prints imported or stored text (memory bridge, decision
    # content, file paths) can otherwise crash on the first non-ASCII character.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass  # already UTF-8, or a stream (e.g. under a test) that cannot be reconfigured
    app()


if __name__ == "__main__":
    main()
