"""Investigation API endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from osintsuite.db.repository import Repository
from osintsuite.web.dependencies import get_repo
from osintsuite.web.schemas import InvestigationCreate, InvestigationResponse

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
