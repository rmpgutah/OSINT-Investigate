"""Investigation API endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from osintsuite.db.repository import Repository
from osintsuite.engine.investigation import InvestigationEngine
from osintsuite.web.dependencies import get_engine, get_repo
from osintsuite.web.schemas import InvestigationCreate, InvestigationResponse, InvestigationUpdate

router = APIRouter()


@router.post("/", response_model=InvestigationResponse)
async def create_investigation(
    data: InvestigationCreate, repo: Repository = Depends(get_repo)
):
    inv = await repo.create_investigation(data.title, data.description)
    return InvestigationResponse(
        id=inv.id,
        case_number=inv.case_number,
        title=inv.title,
        description=inv.description,
        status=inv.status,
        created_at=inv.created_at,
        updated_at=inv.updated_at,
    )


@router.get("/", response_model=list[InvestigationResponse])
async def list_investigations(
    status: str | None = None, repo: Repository = Depends(get_repo)
):
    investigations = await repo.list_investigations(status)
    return [
        InvestigationResponse(
            id=inv.id,
            case_number=inv.case_number,
            title=inv.title,
            description=inv.description,
            status=inv.status,
            created_at=inv.created_at,
            updated_at=inv.updated_at,
        )
        for inv in investigations
    ]


@router.get("/{investigation_id}", response_model=InvestigationResponse)
async def get_investigation(
    investigation_id: uuid.UUID, repo: Repository = Depends(get_repo)
):
    inv = await repo.get_investigation(investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return InvestigationResponse(
        id=inv.id,
        case_number=inv.case_number,
        title=inv.title,
        description=inv.description,
        status=inv.status,
        created_at=inv.created_at,
        updated_at=inv.updated_at,
    )


@router.patch("/{investigation_id}/status")
async def update_status(
    investigation_id: uuid.UUID,
    status: str,
    repo: Repository = Depends(get_repo),
):
    await repo.update_investigation_status(investigation_id, status)
    return {"status": "updated"}


@router.patch("/{investigation_id}")
async def update_investigation(
    investigation_id: uuid.UUID,
    data: InvestigationUpdate,
    repo: Repository = Depends(get_repo),
):
    fields = data.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    await repo.update_investigation(investigation_id, **fields)
    return {"status": "updated"}


@router.delete("/{investigation_id}")
async def delete_investigation(
    investigation_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
):
    inv = await repo.get_investigation(investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")
    await repo.delete_investigation(investigation_id)
    return {"status": "deleted"}


@router.post("/{investigation_id}/run-all")
async def run_all_targets(
    investigation_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
    engine: InvestigationEngine = Depends(get_engine),
):
    """Run all applicable modules on ALL targets in the investigation."""
    targets = await repo.list_targets(investigation_id)
    if not targets:
        raise HTTPException(status_code=400, detail="No targets in investigation")

    total_findings = 0
    target_results = {}
    for target in targets:
        try:
            results = await engine.run_all_applicable(target.id)
            count = sum(len(f) for f in results.values())
            total_findings += count
            target_results[target.label] = {
                "modules_run": list(results.keys()),
                "findings": count,
            }
        except Exception as e:
            target_results[target.label] = {"error": str(e), "findings": 0}

    return {
        "targets_processed": len(targets),
        "total_findings": total_findings,
        "details": target_results,
    }
