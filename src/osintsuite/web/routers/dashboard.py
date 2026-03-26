"""Dashboard HTML views."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from osintsuite.db.repository import Repository
from osintsuite.web.dependencies import get_repo

router = APIRouter()

from pathlib import Path
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, repo: Repository = Depends(get_repo)):
    investigations = await repo.list_investigations()
    stats = {
        "total_cases": len(investigations),
        "open_cases": sum(1 for i in investigations if i.status == "open"),
        "active_cases": sum(1 for i in investigations if i.status == "active"),
        "closed_cases": sum(1 for i in investigations if i.status == "closed"),
    }
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "investigations": investigations, "stats": stats},
    )


@router.get("/investigation/{case_number}", response_class=HTMLResponse)
async def investigation_detail(
    request: Request, case_number: str, repo: Repository = Depends(get_repo)
):
    inv = await repo.get_investigation_by_case(case_number)
    if not inv:
        return HTMLResponse("<h1>Case not found</h1>", status_code=404)

    inv_full = await repo.get_investigation_full(inv.id)
    return templates.TemplateResponse(
        "investigation_detail.html",
        {"request": request, "investigation": inv_full},
    )


@router.get("/target/{target_id}/profile", response_class=HTMLResponse)
async def target_profile(
    request: Request, target_id: str, repo: Repository = Depends(get_repo)
):
    from uuid import UUID

    target = await repo.get_target(UUID(target_id))
    if not target:
        return HTMLResponse("<h1>Target not found</h1>", status_code=404)

    findings = await repo.get_findings_by_target(target.id)
    modules = {}
    for f in findings:
        modules.setdefault(f.module_name, []).append(f)

    return templates.TemplateResponse(
        "target_profile.html",
        {"request": request, "target": target, "findings_by_module": modules},
    )
