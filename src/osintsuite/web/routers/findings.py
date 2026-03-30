"""Findings API endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from osintsuite.db.repository import Repository
from osintsuite.web.dependencies import get_repo
from osintsuite.web.schemas import FindingResponse, FindingUpdate

router = APIRouter()


@router.get("/", response_model=list[FindingResponse])
async def list_findings(
    target_id: uuid.UUID,
    module_name: str | None = None,
    repo: Repository = Depends(get_repo),
):
    findings = await repo.get_findings_by_target(target_id, module_name)
    return [FindingResponse.model_validate(f) for f in findings]


@router.get("/{finding_id}", response_model=FindingResponse)
async def get_finding(
    finding_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
):
    finding = await repo.get_finding(finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")
    return FindingResponse.model_validate(finding)


@router.patch("/{finding_id}")
async def update_finding(
    finding_id: uuid.UUID,
    data: FindingUpdate,
    repo: Repository = Depends(get_repo),
):
    finding = await repo.get_finding(finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")
    fields = data.model_dump(exclude_none=True)
    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")
    await repo.update_finding(finding_id, **fields)
    return {"status": "updated"}


@router.delete("/{finding_id}")
async def delete_finding(
    finding_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
):
    finding = await repo.get_finding(finding_id)
    if not finding:
        raise HTTPException(status_code=404, detail="Finding not found")
    await repo.delete_finding(finding_id)
    return {"status": "deleted"}
