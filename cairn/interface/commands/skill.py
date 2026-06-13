"""cairn skill, load and print skill prompts (no LLM execution)."""
from __future__ import annotations

import typer
from rich.console import Console
from rich.markdown import Markdown

skill_app = typer.Typer(help="Read built-in or project-local skill prompts")
console = Console()


@skill_app.command("show")
def skill_show(
    alias_or_name: str = typer.Argument(..., help="Skill alias like @cr or folder name"),
    raw: bool = typer.Option(False, "--raw", help="Print raw markdown instead of rendering"),
):
    """Print a skill's system-prompt markdown."""
    from cairn.skills.registry import SkillRegistry

    reg = SkillRegistry()
    content = reg.load(alias_or_name)
    if content is None:
        console.print(f"[yellow]Skill not found:[/yellow] {alias_or_name}")
        raise typer.Exit(code=1)
    if raw:
        typer.echo(content)
    else:
        console.print(Markdown(content))


@skill_app.command("path")
def skill_path(alias_or_name: str = typer.Argument(...)):
    """Print the on-disk path of a skill (for piping to other tools)."""
    from pathlib import Path
    from cairn.skills.registry import SKILL_ALIASES, BUILTIN_SKILLS_DIR

    token = alias_or_name.strip()
    name = SKILL_ALIASES.get(token, token.lstrip("@"))
    custom = Path.cwd() / ".cairn" / "skills" / name / "SKILL.md"
    builtin = BUILTIN_SKILLS_DIR / name / "SKILL.md"
    if custom.exists():
        typer.echo(str(custom))
    elif builtin.exists():
        typer.echo(str(builtin))
    else:
        console.print(f"[yellow]Skill not found:[/yellow] {alias_or_name}")
        raise typer.Exit(code=1)
