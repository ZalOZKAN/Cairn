"""cairn skills, list available skills."""
from __future__ import annotations

from rich import box
from rich.console import Console
from rich.table import Table

console = Console()


def skills_cmd():
    """List available skills (built-in + project-local)."""
    from cairn.skills.registry import SkillRegistry

    reg = SkillRegistry()
    skill_list = reg.list_skills()

    table = Table(title="Cairn Skills", box=box.ROUNDED)
    table.add_column("Alias", style="cyan")
    table.add_column("Name", style="white")
    table.add_column("Status", justify="center")
    table.add_column("Source", style="dim")

    for s in skill_list:
        status = "[green]ok[/green]" if s["available"] else "[red]missing[/red]"
        table.add_row(s["alias"], s["name"], status, s["source"])

    console.print(table)
    console.print("\n[dim]Add a custom skill: .cairn/skills/<name>/SKILL.md[/dim]")
