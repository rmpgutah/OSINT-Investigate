"""CLI commands for database management."""

import asyncio
import json
import subprocess
import sys

import typer
from rich.console import Console

from osintsuite.config import get_settings

console = Console()
app = typer.Typer(no_args_is_help=True)


@app.command("init")
def init_db():
    """Initialize the database (run migrations)."""
    console.print("[bold]Running database migrations...[/bold]")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        console.print("[green]Database initialized successfully.[/green]")
    else:
        console.print(f"[red]Migration failed:[/red]\n{result.stderr}")
        raise typer.Exit(1)


@app.command("migrate")
def create_migration(
    message: str = typer.Argument(help="Migration message"),
):
    """Create a new database migration."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "revision", "--autogenerate", "-m", message],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        console.print(f"[green]Migration created: {message}[/green]")
        console.print(result.stdout)
    else:
        console.print(f"[red]Migration creation failed:[/red]\n{result.stderr}")
        raise typer.Exit(1)


@app.command("export")
def export_case(
    case_number: str = typer.Argument(help="Case number to export"),
    output: str = typer.Option("", "--output", "-o", help="Output file path"),
):
    """Export a full investigation as JSON."""
    from osintsuite.db.repository import Repository
    from osintsuite.db.session import get_async_session_factory

    async def _export():
        settings = get_settings()
        session_factory = get_async_session_factory(settings)
        async with session_factory() as session:
            repo = Repository(session)
            inv = await repo.get_investigation_by_case(case_number)
            if not inv:
                console.print(f"[red]Case {case_number} not found.[/red]")
                raise typer.Exit(1)

            inv_full = await repo.get_investigation_full(inv.id)

            data = {
                "case_number": inv_full.case_number,
                "title": inv_full.title,
                "description": inv_full.description,
                "status": inv_full.status,
                "created_at": inv_full.created_at.isoformat(),
                "targets": [
                    {
                        "id": str(t.id),
                        "type": t.target_type,
                        "label": t.label,
                        "full_name": t.full_name,
                        "email": t.email,
                        "phone": t.phone,
                        "city": t.city,
                        "state": t.state,
                        "findings": [
                            {
                                "module": f.module_name,
                                "source": f.source,
                                "type": f.finding_type,
                                "title": f.title,
                                "content": f.content,
                                "data": f.data,
                                "confidence": f.confidence,
                            }
                            for f in t.findings
                        ],
                    }
                    for t in inv_full.targets
                ],
            }

            output_path = output or f"{case_number}_export.json"
            with open(output_path, "w") as f:
                json.dump(data, f, indent=2, default=str)

            console.print(f"[green]Exported to {output_path}[/green]")

    asyncio.run(_export())
