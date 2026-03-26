"""Report API endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from osintsuite.config import Settings, get_settings
from osintsuite.db.repository import Repository
from osintsuite.reporting.generator import ReportGenerator
from osintsuite.web.dependencies import get_repo
from osintsuite.web.schemas import ReportRequest, ReportResponse

router = APIRouter()


@router.post("/", response_model=ReportResponse)
async def generate_report(
    investigation_id: uuid.UUID,
    data: ReportRequest,
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_settings),
):
    inv = await repo.get_investigation(investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")

    generator = ReportGenerator(repo, settings)
    output_path = await generator.generate(investigation_id, data.format)

    reports = await repo.list_reports(investigation_id)
    latest = reports[0] if reports else None
    if not latest:
        raise HTTPException(status_code=500, detail="Report generation failed")

    return ReportResponse.model_validate(latest)


@router.get("/", response_model=list[ReportResponse])
async def list_reports(
    investigation_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
):
    reports = await repo.list_reports(investigation_id)
    return [ReportResponse.model_validate(r) for r in reports]


@router.get("/{report_id}/download")
async def download_report(
    report_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
):
    # Get report from DB
    from sqlalchemy import select
    from osintsuite.db.models import Report

    result = await repo.session.execute(select(Report).where(Report.id == report_id))
    report = result.scalar_one_or_none()
    if not report or not report.file_path:
        raise HTTPException(status_code=404, detail="Report not found")

    from pathlib import Path

    path = Path(report.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report file not found on disk")

    media_types = {
        "csv": "text/csv",
        "html": "text/html",
        "json": "application/json",
        "pdf": "application/pdf",
    }
    return FileResponse(
        path=str(path),
        media_type=media_types.get(report.format, "application/octet-stream"),
        filename=path.name,
    )
