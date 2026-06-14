"""cairn version, version info."""
from rich.console import Console

console = Console()


def version_cmd():
    """Print Cairn version info."""
    from cairn import __version__
    console.print(f"[bold]Cairn[/bold] v{__version__}")
    console.print("[dim]Portable .cairn/ folder for any AI coding agent[/dim]")
