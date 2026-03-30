"""Target API endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from pydantic import BaseModel

from osintsuite.config import Settings, get_settings
from osintsuite.db.repository import Repository
from osintsuite.engine.investigation import InvestigationEngine
from osintsuite.web.dependencies import get_engine, get_repo
from osintsuite.web.schemas import ModuleRunRequest, TargetCreate, TargetResponse, TargetUpdate

router = APIRouter()

VALID_TARGET_TYPES = {"person", "domain", "email", "phone", "username", "ip", "organization"}


class BatchImportRequest(BaseModel):
    investigation_id: uuid.UUID
    csv_data: str


@router.post("/import")
async def batch_import_targets(data: BatchImportRequest, repo: Repository = Depends(get_repo)):
    """Import multiple targets from CSV text."""
    import csv
    import io

    reader = csv.DictReader(io.StringIO(data.csv_data))
    created = []
    errors = []

    for i, row in enumerate(reader, 1):
        try:
            target = await repo.add_target(
                investigation_id=data.investigation_id,
                target_type=row.get("type", "person"),
                label=row.get("label", f"Import-{i}"),
                full_name=row.get("name") or row.get("full_name"),
                email=row.get("email"),
                phone=row.get("phone"),
                city=row.get("city"),
                state=row.get("state"),
            )
            created.append({"id": str(target.id), "label": target.label})
        except Exception as e:
            errors.append({"row": i, "error": str(e)})

    return {"created": len(created), "errors": errors, "targets": created}


@router.post("/", response_model=TargetResponse)
async def create_target(data: TargetCreate, repo: Repository = Depends(get_repo)):
    # Pydantic already validates target_type via pattern and label via min_length,
    # but add explicit checks for defense-in-depth
    if not data.label or not data.label.strip():
        raise HTTPException(status_code=400, detail="Label must not be empty")
    if data.target_type not in VALID_TARGET_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid target type '{data.target_type}'. Must be one of: {', '.join(sorted(VALID_TARGET_TYPES))}",
        )
    target = await repo.add_target(
        investigation_id=data.investigation_id,
        target_type=data.target_type,
        label=data.label,
        full_name=data.full_name,
        email=data.email,
        phone=data.phone,
        address=data.address,
        date_of_birth=data.date_of_birth,
        city=data.city,
        state=data.state,
    )
    return TargetResponse.model_validate(target)


@router.get("/", response_model=list[TargetResponse])
async def list_targets(
    investigation_id: uuid.UUID | None = None,
    repo: Repository = Depends(get_repo),
):
    targets = await repo.list_targets(investigation_id)
    return [TargetResponse.model_validate(t) for t in targets]


@router.get("/{target_id}", response_model=TargetResponse)
async def get_target(target_id: uuid.UUID, repo: Repository = Depends(get_repo)):
    target = await repo.get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    return TargetResponse.model_validate(target)


@router.get("/{target_id}/summary")
async def get_target_summary(target_id: uuid.UUID, repo: Repository = Depends(get_repo)):
    """Return target info + finding count + module run count + last run time + top 3 findings."""
    from sqlalchemy import select
    from osintsuite.db.models import ModuleRun

    target = await repo.get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    findings = await repo.get_findings_by_target(target_id)

    # Module runs
    q = (
        select(ModuleRun)
        .where(ModuleRun.target_id == target_id)
        .order_by(ModuleRun.started_at.desc())
    )
    result = await repo.session.execute(q)
    runs = result.scalars().all()

    last_run_time = None
    if runs:
        latest = runs[0]
        last_run_time = (latest.completed_at or latest.started_at)
        if last_run_time:
            last_run_time = last_run_time.isoformat()

    # Top 3 findings by confidence (highest first)
    sorted_findings = sorted(
        findings,
        key=lambda f: f.confidence if f.confidence is not None else -1,
        reverse=True,
    )
    top_findings = [
        {
            "id": str(f.id),
            "module_name": f.module_name,
            "title": f.title,
            "confidence": f.confidence,
            "source": f.source,
        }
        for f in sorted_findings[:3]
    ]

    return {
        "target": {
            "id": str(target.id),
            "label": target.label,
            "target_type": target.target_type,
            "full_name": target.full_name,
            "email": target.email,
            "phone": target.phone,
            "created_at": target.created_at.isoformat(),
        },
        "finding_count": len(findings),
        "module_run_count": len(runs),
        "last_run_time": last_run_time,
        "top_findings": top_findings,
    }


@router.post("/{target_id}/run")
async def run_modules(
    target_id: uuid.UUID,
    data: ModuleRunRequest,
    engine: InvestigationEngine = Depends(get_engine),
):
    try:
        if data.module_name:
            findings = await engine.run_module(target_id, data.module_name)
            return {
                "module": data.module_name,
                "findings_count": len(findings),
            }
        else:
            results = await engine.run_all_applicable(target_id)
            return {
                "modules_run": list(results.keys()),
                "total_findings": sum(len(f) for f in results.values()),
                "details": {k: len(v) for k, v in results.items()},
            }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"error": "Module execution failed", "message": str(e)},
        )


@router.get("/search/{query}", response_model=list[TargetResponse])
async def search_targets(query: str, repo: Repository = Depends(get_repo)):
    targets = await repo.search_targets(query)
    return [TargetResponse.model_validate(t) for t in targets]


@router.patch("/{target_id}")
async def update_target(
    target_id: uuid.UUID,
    data: TargetUpdate,
    repo: Repository = Depends(get_repo),
):
    target = await repo.get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    fields = data.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    # Validate label is not empty whitespace if provided
    if "label" in fields and not fields["label"].strip():
        raise HTTPException(status_code=400, detail="Label must not be empty")
    await repo.update_target(target_id, **fields)
    return {"status": "updated"}


@router.delete("/{target_id}")
async def delete_target(
    target_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
):
    target = await repo.get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    await repo.delete_target(target_id)
    return {"status": "deleted"}


@router.get("/{target_id}/run-status")
async def get_run_status(target_id: uuid.UUID, repo: Repository = Depends(get_repo)):
    """Get module run statuses for progress tracking."""
    from sqlalchemy import select
    from osintsuite.db.models import ModuleRun

    q = select(ModuleRun).where(ModuleRun.target_id == target_id).order_by(ModuleRun.started_at.desc())
    result = await repo.session.execute(q)
    runs = result.scalars().all()
    return [
        {
            "id": str(r.id),
            "module_name": r.module_name,
            "status": r.status,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "findings_count": r.findings_count,
            "error_message": r.error_message,
        }
        for r in runs
    ]


@router.get("/{target_id}/export")
async def export_target_findings(
    target_id: uuid.UUID,
    format: str = "json",
    repo: Repository = Depends(get_repo),
):
    """Export all findings for a single target as CSV or JSON."""
    target = await repo.get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    findings = await repo.get_findings_by_target(target_id)

    if format == "csv":
        import csv
        import io
        from fastapi.responses import StreamingResponse

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["module", "source", "type", "title", "content", "confidence", "flagged", "reviewed", "created_at"])
        for f in findings:
            writer.writerow([
                f.module_name, f.source, f.finding_type,
                f.title or "", (f.content or "")[:500],
                f.confidence, f.is_flagged, f.is_reviewed,
                f.created_at.isoformat(),
            ])
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={target.label}_findings.csv"},
        )

    # Default: JSON
    return [
        {
            "id": str(f.id),
            "module_name": f.module_name,
            "source": f.source,
            "finding_type": f.finding_type,
            "title": f.title,
            "content": f.content,
            "data": f.data,
            "confidence": f.confidence,
            "is_flagged": f.is_flagged,
            "is_reviewed": f.is_reviewed,
            "created_at": f.created_at.isoformat(),
        }
        for f in findings
    ]
