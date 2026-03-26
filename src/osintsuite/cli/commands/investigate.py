"""CLI commands for managing investigations (cases)."""

import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from osintsuite.config import get_settings
from osintsuite.db.repository import Repository
from osintsuite.db.session import get_async_session_factory

console = Console()
app = typer.Typer(no_args_is_help=True)


@app.command("new")
def new_case(
    title: str = typer.Argument(help="Investigation title"),
    description: Optional[str] = typer.Option(None, "--desc", "-d", help="Case description"),
):
    """Create a new investigation case."""

    async def _create():
        settings = get_settings()
        session_factory = get_async_session_factory(settings)
        async with session_factory() as session:
            repo = Repository(session)
            inv = await repo.create_investigation(title, description)
            await session.commit()
            console.print(f"[green]Created investigation:[/green]")
            console.print(f"  Case Number: [bold cyan]{inv.case_number}[/bold cyan]")
            console.print(f"  Title: {inv.title}")
            console.print(f"  ID: {inv.id}")

    asyncio.run(_create())


@app.command("list")
def list_cases(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter by status"),
):
    """List all investigations."""

    async def _list():
        settings = get_settings()
        session_factory = get_async_session_factory(settings)
        async with session_factory() as session:
            repo = Repository(session)
            investigations = await repo.list_investigations(status)

            if not investigations:
                console.print("[yellow]No investigations found.[/yellow]")
                return

            table = Table(title="Investigations")
            table.add_column("Case #", style="cyan")
            table.add_column("Title")
            table.add_column("Status", style="green")
            table.add_column("Created")

            for inv in investigations:
                table.add_row(
                    inv.case_number,
                    inv.title,
                    inv.status,
                    inv.created_at.strftime("%Y-%m-%d %H:%M"),
                )

            console.print(table)

    asyncio.run(_list())


@app.command("show")
def show_case(
    case_number: str = typer.Argument(help="Case number (e.g., CASE-0001)"),
):
    """Show detailed case information."""

    async def _show():
        settings = get_settings()
        session_factory = get_async_session_factory(settings)
        async with session_factory() as session:
            repo = Repository(session)
            inv = await repo.get_investigation_by_case(case_number)
            if not inv:
                console.print(f"[red]Case {case_number} not found.[/red]")
                raise typer.Exit(1)

            inv_full = await repo.get_investigation_full(inv.id)

            console.print(f"\n[bold cyan]{inv_full.case_number}[/bold cyan] — {inv_full.title}")
            console.print(f"Status: [green]{inv_full.status}[/green]")
            if inv_full.description:
                console.print(f"Description: {inv_full.description}")
            console.print(f"Created: {inv_full.created_at.strftime('%Y-%m-%d %H:%M')}")

            if inv_full.targets:
                console.print(f"\n[bold]Targets ({len(inv_full.targets)}):[/bold]")
                for target in inv_full.targets:
                    finding_count = len(target.findings)
                    console.print(
                        f"  [{target.target_type}] {target.label} "
                        f"— {finding_count} findings (ID: {target.id})"
                    )

            if inv_full.reports:
                console.print(f"\n[bold]Reports ({len(inv_full.reports)}):[/bold]")
                for report in inv_full.reports:
                    console.print(f"  {report.title} ({report.format}) — {report.file_path}")

    asyncio.run(_show())


@app.command("close")
def close_case(
    case_number: str = typer.Argument(help="Case number to close"),
):
    """Close an investigation."""

    async def _close():
        settings = get_settings()
        session_factory = get_async_session_factory(settings)
        async with session_factory() as session:
            repo = Repository(session)
            inv = await repo.get_investigation_by_case(case_number)
            if not inv:
                console.print(f"[red]Case {case_number} not found.[/red]")
                raise typer.Exit(1)

            await repo.update_investigation_status(inv.id, "closed")
            await session.commit()
            console.print(f"[green]Case {case_number} closed.[/green]")

    asyncio.run(_close())
