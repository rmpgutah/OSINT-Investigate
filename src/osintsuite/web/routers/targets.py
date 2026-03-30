"""Target API endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from osintsuite.config import Settings, get_settings
from osintsuite.db.repository import Repository
from osintsuite.engine.investigation import InvestigationEngine
from osintsuite.web.dependencies import get_engine, get_repo
from osintsuite.web.schemas import ModuleRunRequest, TargetCreate, TargetResponse, TargetUpdate

router = APIRouter()


@router.post("/", response_model=TargetResponse)
async def create_target(data: TargetCreate, repo: Repository = Depends(get_repo)):
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
