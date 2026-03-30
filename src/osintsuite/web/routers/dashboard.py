"""Dashboard HTML views."""

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from osintsuite.db.repository import Repository
from osintsuite.web.dependencies import get_repo

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def dashboard(request: Request, repo: Repository = Depends(get_repo)):
    from sqlalchemy import select, func as sqlfunc
    from osintsuite.db.models import Target, Finding

    investigations = await repo.list_investigations()

    # Enrich with target/finding counts
    enriched = []
    for inv in investigations:
        tgt_count = (await repo.session.execute(
            select(sqlfunc.count(Target.id)).where(Target.investigation_id == inv.id)
        )).scalar_one()

        # Count findings across all targets in this investigation
        finding_count = (await repo.session.execute(
            select(sqlfunc.count(Finding.id))
            .join(Target, Finding.target_id == Target.id)
            .where(Target.investigation_id == inv.id)
        )).scalar_one()

        enriched.append({
            "inv": inv,
            "target_count": tgt_count,
            "finding_count": finding_count,
        })

    stats = {
        "total_cases": len(investigations),
        "open_cases": sum(1 for i in investigations if i.status == "open"),
        "active_cases": sum(1 for i in investigations if i.status == "active"),
        "closed_cases": sum(1 for i in investigations if i.status == "closed"),
    }
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        context={"investigations": enriched, "stats": stats},
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
        request,
        "investigation_detail.html",
        context={"investigation": inv_full},
    )


@router.get("/investigation/{case_number}/summary", response_class=HTMLResponse)
async def investigation_summary(
    request: Request, case_number: str, repo: Repository = Depends(get_repo)
):
    inv = await repo.get_investigation_by_case(case_number)
    if not inv:
        return HTMLResponse("<h1>Case not found</h1>", status_code=404)

    inv_full = await repo.get_investigation_full(inv.id)

    # Compute stats for the template
    all_findings = []
    for t in inv_full.targets:
        all_findings.extend(t.findings)

    flagged_count = sum(1 for f in all_findings if f.is_flagged)
    reviewed_count = sum(1 for f in all_findings if f.is_reviewed)

    # Top 10 flagged findings
    flagged_findings = sorted(
        [f for f in all_findings if f.is_flagged],
        key=lambda f: f.created_at,
        reverse=True,
    )[:10]

    return templates.TemplateResponse(
        request,
        "investigation_summary.html",
        context={
            "investigation": inv_full,
            "total_targets": len(inv_full.targets),
            "total_findings": len(all_findings),
            "flagged_count": flagged_count,
            "reviewed_count": reviewed_count,
            "flagged_findings": flagged_findings,
        },
    )


@router.get("/target/{target_id}/profile", response_class=HTMLResponse)
async def target_profile(
    request: Request, target_id: str, repo: Repository = Depends(get_repo)
):
    from uuid import UUID

    target = await repo.get_target(UUID(target_id))
    if not target:
        return HTMLResponse("<h1>Target not found</h1>", status_code=404)

    # Load investigation for breadcrumbs
    investigation = await repo.get_investigation(target.investigation_id)

    findings = await repo.get_findings_by_target(target.id)
    modules = {}
    for f in findings:
        modules.setdefault(f.module_name, []).append(f)

    return templates.TemplateResponse(
        request,
        "target_profile.html",
        context={"target": target, "findings_by_module": modules, "investigation": investigation},
    )
