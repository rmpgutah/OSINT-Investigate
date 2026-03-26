"""Main CLI application — entry point for the `osint` command."""

import asyncio
import logging
from typing import Optional

import typer
from rich.console import Console

from osintsuite.cli.commands.db_cmd import app as db_app
from osintsuite.cli.commands.investigate import app as case_app
from osintsuite.cli.commands.report import app as report_app
from osintsuite.cli.commands.target import app as target_app

console = Console()

app = typer.Typer(
    name="osint",
    help="OSINT Investigation Suite — CLI for intelligence gathering and case management",
    no_args_is_help=True,
)

app.add_typer(case_app, name="case", help="Manage investigations/cases")
app.add_typer(target_app, name="target", help="Manage investigation targets")
app.add_typer(report_app, name="report", help="Generate investigation reports")
app.add_typer(db_app, name="db", help="Database utilities")


@app.command()
def run(
    case_number: str = typer.Argument(help="Case number (e.g., CASE-0001)"),
    target_id: str = typer.Option(..., "--target", "-t", help="Target UUID"),
    module: Optional[str] = typer.Option(
        None, "--module", "-m", help="Specific module to run (default: all applicable)"
    ),
):
    """Run OSINT modules against a target."""
    from uuid import UUID

    from osintsuite.config import get_settings
    from osintsuite.db.repository import Repository
    from osintsuite.db.session import get_async_session_factory
    from osintsuite.engine.investigation import InvestigationEngine

    async def _run():
        settings = get_settings()
        session_factory = get_async_session_factory(settings)
        async with session_factory() as session:
            repo = Repository(session)
            engine = InvestigationEngine(repo, settings)

            try:
                tid = UUID(target_id)
                if module:
                    console.print(f"[bold]Running module '{module}' on target {target_id}...[/bold]")
                    findings = await engine.run_module(tid, module)
                    console.print(f"[green]Completed: {len(findings)} findings[/green]")
                else:
                    console.print(f"[bold]Running all modules on target {target_id}...[/bold]")
                    results = await engine.run_all_applicable(tid)
                    total = sum(len(f) for f in results.values())
                    for mod_name, findings in results.items():
                        status = "[green]OK[/green]" if findings else "[yellow]no results[/yellow]"
                        console.print(f"  {mod_name}: {len(findings)} findings {status}")
                    console.print(f"\n[green]Total: {total} findings[/green]")

                await session.commit()
            finally:
                await engine.close()

    asyncio.run(_run())


@app.command()
def modules():
    """List available OSINT modules."""
    from osintsuite.config import get_settings
    from osintsuite.db.repository import Repository
    from osintsuite.engine.investigation import InvestigationEngine

    from rich.table import Table

    # We just need module info, no DB needed
    settings = get_settings()
    # Create a minimal engine just for listing
    import httpx
    from osintsuite.modules.base import RateLimiter

    table = Table(title="Available OSINT Modules")
    table.add_column("Name", style="cyan")
    table.add_column("Description")
    table.add_column("Target Types", style="green")

    client = httpx.AsyncClient()
    limiter = RateLimiter()
    from osintsuite.modules.domain_recon import DomainReconModule
    from osintsuite.modules.email_intel import EmailIntelModule
    from osintsuite.modules.person_search import PersonSearchModule
    from osintsuite.modules.phone_lookup import PhoneLookupModule
    from osintsuite.modules.social_media import SocialMediaModule
    from osintsuite.modules.web_scraper import WebScraperModule

    all_modules = [
        PersonSearchModule(client, limiter),
        WebScraperModule(client, limiter),
        EmailIntelModule(client, limiter),
        PhoneLookupModule(client, limiter),
        DomainReconModule(client, limiter),
        SocialMediaModule(client, limiter),
    ]
    for mod in all_modules:
        table.add_row(mod.name, mod.description, ", ".join(mod.applicable_target_types()))

    console.print(table)


@app.command()
def version():
    """Show version information."""
    from osintsuite import __version__

    console.print(f"OSINT Investigation Suite v{__version__}")


@app.callback()
def main_callback(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose output"),
):
    """OSINT Investigation Suite — Open-source intelligence gathering platform."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")


if __name__ == "__main__":
    app()
