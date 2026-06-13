"""cairn workflow, load and print workflow prompts (no LLM execution)."""
from __future__ import annotations

import typer
from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

workflow_app = typer.Typer(help="Read built-in or project-local workflow prompts")
console = Console()


@workflow_app.command("show")
def workflow_show(
    name: str = typer.Argument(..., help="Workflow name, with or without leading slash"),
    raw: bool = typer.Option(False, "--raw", help="Print raw markdown"),
):
    """Print a workflow's system-prompt markdown."""
    from cairn.workflows.runner import WorkflowRunner

    runner = WorkflowRunner()
    content = runner.load(name)
    if content is None:
        console.print(f"[yellow]Workflow not found:[/yellow] {name}")
        raise typer.Exit(code=1)
    if raw:
        typer.echo(content)
    else:
        console.print(Markdown(content))


@workflow_app.command("list")
def workflow_list():
    """List built-in and project-local workflows."""
    from cairn.workflows.runner import WorkflowRunner

    runner = WorkflowRunner()
    names = runner.list_workflows()
    if not names:
        console.print("[yellow]No workflows found.[/yellow]")
        return
    table = Table(title="Workflows", box=box.ROUNDED)
    table.add_column("name", style="cyan")
    for n in names:
        table.add_row(f"/{n}")
    console.print(table)
