"""Findings API endpoints."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from osintsuite.db.repository import Repository
from osintsuite.web.dependencies import get_repo
from osintsuite.web.schemas import FindingResponse, FindingUpdate

router = APIRouter()


class BulkFindingUpdateRequest(BaseModel):
    finding_ids: list[uuid.UUID]
    is_flagged: Optional[bool] = None
    is_reviewed: Optional[bool] = None
    tags: Optional[list[str]] = None


@router.get("/stats")
async def get_finding_stats(
    target_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
):
    """Finding statistics for a target: count by module, confidence buckets, flagged, reviewed."""
    target = await repo.get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    stats = await repo.get_finding_stats(target_id)
    return stats


@router.post("/bulk-update")
async def bulk_update_findings(
    data: BulkFindingUpdateRequest,
    repo: Repository = Depends(get_repo),
):
    """Update multiple findings at once (is_flagged, is_reviewed, tags)."""
    if not data.finding_ids:
        raise HTTPException(status_code=400, detail="finding_ids must not be empty")

    fields = {}
    if data.is_flagged is not None:
        fields["is_flagged"] = data.is_flagged
    if data.is_reviewed is not None:
        fields["is_reviewed"] = data.is_reviewed
    if data.tags is not None:
        fields["tags"] = data.tags

    if not fields:
        raise HTTPException(status_code=400, detail="No fields to update")

    updated = await repo.bulk_update_findings(data.finding_ids, **fields)
    return {"status": "updated", "updated_count": updated}


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
