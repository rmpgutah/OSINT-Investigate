"""CLI commands for managing investigation targets."""

import asyncio
from datetime import date
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from osintsuite.config import get_settings
from osintsuite.db.repository import Repository
from osintsuite.db.session import get_async_session_factory

console = Console()
app = typer.Typer(no_args_is_help=True)

TARGET_TYPES = ["person", "domain", "email", "phone", "username", "ip", "organization"]


@app.command("add")
def add_target(
    case_number: str = typer.Argument(help="Case number"),
    target_type: str = typer.Argument(help=f"Target type: {', '.join(TARGET_TYPES)}"),
    name: Optional[str] = typer.Option(None, "--name", "-n", help="Full name"),
    email: Optional[str] = typer.Option(None, "--email", "-e", help="Email address"),
    phone: Optional[str] = typer.Option(None, "--phone", "-p", help="Phone number"),
    address: Optional[str] = typer.Option(None, "--address", "-a", help="Physical address"),
    dob: Optional[str] = typer.Option(None, "--dob", help="Date of birth (YYYY-MM-DD)"),
    city: Optional[str] = typer.Option(None, "--city", help="City"),
    state: Optional[str] = typer.Option(None, "--state", help="State"),
    label: Optional[str] = typer.Option(None, "--label", "-l", help="Display label (default: name or type)"),
):
    """Add a target to an investigation."""
    if target_type not in TARGET_TYPES:
        console.print(f"[red]Invalid target type. Choose from: {', '.join(TARGET_TYPES)}[/red]")
        raise typer.Exit(1)

    async def _add():
        settings = get_settings()
        session_factory = get_async_session_factory(settings)
        async with session_factory() as session:
            repo = Repository(session)
            inv = await repo.get_investigation_by_case(case_number)
            if not inv:
                console.print(f"[red]Case {case_number} not found.[/red]")
                raise typer.Exit(1)

            display_label = label or name or email or phone or f"{target_type} target"
            dob_parsed = date.fromisoformat(dob) if dob else None

            target = await repo.add_target(
                investigation_id=inv.id,
                target_type=target_type,
                label=display_label,
                full_name=name,
                email=email,
                phone=phone,
                address=address,
                date_of_birth=dob_parsed,
                city=city,
                state=state,
            )
            await session.commit()

            console.print(f"[green]Target added to {case_number}:[/green]")
            console.print(f"  Label: [bold]{target.label}[/bold]")
            console.print(f"  Type: {target.target_type}")
            console.print(f"  ID: [cyan]{target.id}[/cyan]")

    asyncio.run(_add())


@app.command("list")
def list_targets(
    case_number: str = typer.Argument(help="Case number"),
):
    """List targets in an investigation."""

    async def _list():
        settings = get_settings()
        session_factory = get_async_session_factory(settings)
        async with session_factory() as session:
            repo = Repository(session)
            inv = await repo.get_investigation_by_case(case_number)
            if not inv:
                console.print(f"[red]Case {case_number} not found.[/red]")
                raise typer.Exit(1)

            targets = await repo.list_targets(inv.id)
            if not targets:
                console.print("[yellow]No targets in this case.[/yellow]")
                return

            table = Table(title=f"Targets — {case_number}")
            table.add_column("ID", style="cyan", max_width=36)
            table.add_column("Type", style="green")
            table.add_column("Label")
            table.add_column("Name")
            table.add_column("Email")
            table.add_column("Phone")

            for t in targets:
                table.add_row(
                    str(t.id)[:8] + "...",
                    t.target_type,
                    t.label,
                    t.full_name or "",
                    t.email or "",
                    t.phone or "",
                )

            console.print(table)

    asyncio.run(_list())


@app.command("search")
def search_targets(
    query: str = typer.Argument(help="Search query"),
):
    """Search targets across all investigations."""

    async def _search():
        settings = get_settings()
        session_factory = get_async_session_factory(settings)
        async with session_factory() as session:
            repo = Repository(session)
            targets = await repo.search_targets(query)

            if not targets:
                console.print(f"[yellow]No targets matching '{query}'.[/yellow]")
                return

            table = Table(title=f"Search Results: '{query}'")
            table.add_column("ID", style="cyan")
            table.add_column("Type", style="green")
            table.add_column("Label")
            table.add_column("Name")

            for t in targets:
                table.add_row(str(t.id)[:8] + "...", t.target_type, t.label, t.full_name or "")

            console.print(table)

    asyncio.run(_search())


@app.command("findings")
def show_findings(
    target_id: str = typer.Argument(help="Target UUID"),
    module: Optional[str] = typer.Option(None, "--module", "-m", help="Filter by module"),
):
    """Show findings for a target."""
    from uuid import UUID

    async def _findings():
        settings = get_settings()
        session_factory = get_async_session_factory(settings)
        async with session_factory() as session:
            repo = Repository(session)
            tid = UUID(target_id)
            findings = await repo.get_findings_by_target(tid, module)

            if not findings:
                console.print("[yellow]No findings.[/yellow]")
                return

            table = Table(title=f"Findings for {target_id[:8]}...")
            table.add_column("Module", style="cyan")
            table.add_column("Source", style="green")
            table.add_column("Type")
            table.add_column("Title", max_width=60)
            table.add_column("Confidence")

            for f in findings:
                conf = f"[{'green' if f.confidence and f.confidence > 70 else 'yellow'}]{f.confidence}%[/]" if f.confidence else "—"
                table.add_row(f.module_name, f.source, f.finding_type, f.title or "", conf)

            console.print(table)

    asyncio.run(_findings())
