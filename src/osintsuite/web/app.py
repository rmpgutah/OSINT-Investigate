"""FastAPI application factory and entry point."""

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from osintsuite.config import get_settings
from osintsuite.web.routers import dashboard, findings, investigations, reports, targets

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="OSINT Investigation Suite — Web Interface",
    )

    # Static files
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # HTML views
    app.include_router(dashboard.router)

    # API routes
    app.include_router(investigations.router, prefix="/api/investigations", tags=["investigations"])
    app.include_router(targets.router, prefix="/api/targets", tags=["targets"])
    app.include_router(findings.router, prefix="/api/findings", tags=["findings"])
    app.include_router(reports.router, prefix="/api/reports", tags=["reports"])

    return app


app = create_app()


def main():
    settings = get_settings()
    uvicorn.run(
        "osintsuite.web.app:app",
        host=settings.web_host,
        port=settings.web_port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
