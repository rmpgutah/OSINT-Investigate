"""Findings API endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from osintsuite.db.repository import Repository
from osintsuite.web.dependencies import get_repo
from osintsuite.web.schemas import FindingResponse

router = APIRouter()


@router.get("/", response_model=list[FindingResponse])
async def list_findings(
    target_id: uuid.UUID,
    module_name: str | None = None,
    repo: Repository = Depends(get_repo),
):
    findings = await repo.get_findings_by_target(target_id, module_name)
    return [FindingResponse.model_validate(f) for f in findings]
