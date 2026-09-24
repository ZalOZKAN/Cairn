"""Cairn CLI, memory file format, no LLM runtime."""
import typer
from rich.console import Console

from cairn.interface.commands.memory import memory_app
from cairn.interface.commands.init import init_cmd
from cairn.interface.commands.mcp import mcp_app
from cairn.interface.commands.version import version_cmd

app = typer.Typer(
    name="cairn",
    help="Cairn, portable .cairn/ folder for any AI coding agent",
    no_args_is_help=True,
)
console = Console()

app.add_typer(memory_app, name="memory")
app.add_typer(mcp_app, name="mcp")
app.command("init")(init_cmd)
app.command("version")(version_cmd)


def main():
    app()


if __name__ == "__main__":
    main()
