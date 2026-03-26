"""CLI commands for generating investigation reports."""

import asyncio
from typing import Optional

import typer
from rich.console import Console

from osintsuite.config import get_settings
from osintsuite.db.repository import Repository
from osintsuite.db.session import get_async_session_factory

console = Console()
app = typer.Typer(no_args_is_help=True)


@app.command("generate")
def generate_report(
    case_number: str = typer.Argument(help="Case number"),
    format: str = typer.Option("html", "--format", "-f", help="Report format: csv, html, json, pdf"),
):
    """Generate a report for an investigation."""

    async def _generate():
        settings = get_settings()
        session_factory = get_async_session_factory(settings)
        async with session_factory() as session:
            repo = Repository(session)
            inv = await repo.get_investigation_by_case(case_number)
            if not inv:
                console.print(f"[red]Case {case_number} not found.[/red]")
                raise typer.Exit(1)

            from osintsuite.reporting.generator import ReportGenerator

            generator = ReportGenerator(repo, settings)
            console.print(f"[bold]Generating {format} report for {case_number}...[/bold]")

            output_path = await generator.generate(inv.id, format)
            await session.commit()

            console.print(f"[green]Report saved to: {output_path}[/green]")

    asyncio.run(_generate())


@app.command("list")
def list_reports(
    case_number: str = typer.Argument(help="Case number"),
):
    """List reports for an investigation."""
    from rich.table import Table

    async def _list():
        settings = get_settings()
        session_factory = get_async_session_factory(settings)
        async with session_factory() as session:
            repo = Repository(session)
            inv = await repo.get_investigation_by_case(case_number)
            if not inv:
                console.print(f"[red]Case {case_number} not found.[/red]")
                raise typer.Exit(1)

            reports = await repo.list_reports(inv.id)
            if not reports:
                console.print("[yellow]No reports generated yet.[/yellow]")
                return

            table = Table(title=f"Reports — {case_number}")
            table.add_column("Title")
            table.add_column("Format", style="cyan")
            table.add_column("Path")
            table.add_column("Generated")

            for r in reports:
                table.add_row(
                    r.title,
                    r.format,
                    r.file_path or "",
                    r.generated_at.strftime("%Y-%m-%d %H:%M"),
                )

            console.print(table)

    asyncio.run(_list())
