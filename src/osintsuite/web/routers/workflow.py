"""Workflow API endpoints — clone, timeline, finding links, dedup, audit, templates."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException

from osintsuite.db.repository import Repository
from osintsuite.web.dependencies import get_repo
from osintsuite.web.schemas import (
    AuditLogResponse,
    CaseTemplate,
    FindingLinkRequest,
    FindingLinkResponse,
    FromTemplateRequest,
    InvestigationResponse,
    TimelineEvent,
)

router = APIRouter()

# ── Predefined case templates ──────────────────────────────────

CASE_TEMPLATES: dict[str, CaseTemplate] = {
    "missing_person": CaseTemplate(
        name="Missing Person",
        description="Investigation for a missing person case",
        target_types=["person"],
    ),
    "fraud_investigation": CaseTemplate(
        name="Fraud Investigation",
        description="Fraud investigation with person and organization targets",
        target_types=["person", "organization"],
    ),
    "background_check": CaseTemplate(
        name="Background Check",
        description="Background check on an individual with extended profile fields",
        target_types=["person"],
    ),
    "domain_investigation": CaseTemplate(
        name="Domain Investigation",
        description="Investigation of domain infrastructure and IP addresses",
        target_types=["domain", "ip"],
    ),
    "cyber_threat": CaseTemplate(
        name="Cyber Threat",
        description="Cyber threat investigation covering domains, IPs, and email indicators",
        target_types=["domain", "ip", "email"],
    ),
}


# ── Clone Investigation ────────────────────────────────────────

@router.post("/clone/{investigation_id}", response_model=InvestigationResponse)
async def clone_investigation(
    investigation_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
):
    """Clone an investigation (copies structure and targets, not findings)."""
    try:
        new_inv = await repo.clone_investigation(investigation_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return InvestigationResponse(
        id=new_inv.id,
        case_number=new_inv.case_number,
        title=new_inv.title,
        description=new_inv.description,
        status=new_inv.status,
        priority=new_inv.priority,
        assigned_to=new_inv.assigned_to,
        tags=new_inv.tags or [],
        classification=new_inv.classification,
        created_at=new_inv.created_at,
        updated_at=new_inv.updated_at,
    )


# ── Timeline ───────────────────────────────────────────────────

@router.get("/timeline/{investigation_id}", response_model=list[TimelineEvent])
async def get_timeline(
    investigation_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
):
    """Get a chronological timeline of all events in an investigation."""
    inv = await repo.get_investigation(investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")
    events = await repo.get_investigation_timeline(investigation_id)
    return [TimelineEvent(**e) for e in events]


# ── Finding Links ──────────────────────────────────────────────

@router.post("/link-findings", response_model=FindingLinkResponse)
async def link_findings(
    data: FindingLinkRequest,
    repo: Repository = Depends(get_repo),
):
    """Create a relationship link between two findings."""
    # Validate both findings exist
    fa = await repo.get_finding(data.finding_a_id)
    fb = await repo.get_finding(data.finding_b_id)
    if not fa or not fb:
        raise HTTPException(status_code=404, detail="One or both findings not found")
    link = await repo.link_findings(data.finding_a_id, data.finding_b_id, data.relationship)
    return FindingLinkResponse(
        id=link.id,
        finding_a_id=link.finding_a_id,
        finding_b_id=link.finding_b_id,
        relationship=link.relationship_type,
        created_at=link.created_at,
    )


@router.get("/finding-links/{finding_id}", response_model=list[FindingLinkResponse])
async def get_finding_links(
    finding_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
):
    """Get all links associated with a finding."""
    links = await repo.get_finding_links(finding_id)
    return [
        FindingLinkResponse(
            id=link.id,
            finding_a_id=link.finding_a_id,
            finding_b_id=link.finding_b_id,
            relationship=link.relationship_type,
            created_at=link.created_at,
        )
        for link in links
    ]


# ── Deduplication ──────────────────────────────────────────────

@router.post("/deduplicate/{target_id}")
async def deduplicate_findings(
    target_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
):
    """Deduplicate findings for a target — keeps highest confidence per title+source."""
    target = await repo.get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
    result = await repo.deduplicate_findings(target_id)
    return result


# ── Audit Log ──────────────────────────────────────────────────

@router.get("/audit-log", response_model=list[AuditLogResponse])
async def get_audit_log(
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    limit: int = 50,
    repo: Repository = Depends(get_repo),
):
    """Get audit log entries with optional filters."""
    entries = await repo.get_audit_log(
        entity_type=entity_type, entity_id=entity_id, limit=limit
    )
    return [
        AuditLogResponse(
            id=e.id,
            entity_type=e.entity_type,
            entity_id=e.entity_id,
            action=e.action,
            details=e.details,
            created_at=e.created_at,
        )
        for e in entries
    ]


# ── Case Templates ─────────────────────────────────────────────

@router.get("/templates", response_model=list[CaseTemplate])
async def list_templates():
    """Return all predefined case templates."""
    return list(CASE_TEMPLATES.values())


@router.post("/from-template", response_model=InvestigationResponse)
async def create_from_template(
    data: FromTemplateRequest,
    repo: Repository = Depends(get_repo),
):
    """Create a new investigation from a predefined template."""
    template = CASE_TEMPLATES.get(data.template_name)
    if not template:
        valid = ", ".join(CASE_TEMPLATES.keys())
        raise HTTPException(
            status_code=400,
            detail=f"Unknown template '{data.template_name}'. Valid: {valid}",
        )

    inv = await repo.create_investigation(
        title=data.title,
        description=data.description or template.description,
    )

    # Pre-create targets based on template
    for target_type in template.target_types:
        label = f"{target_type.title()} Target"
        await repo.add_target(
            investigation_id=inv.id,
            target_type=target_type,
            label=label,
        )

    await repo.log_audit("investigation", inv.id, "created", {
        "template": data.template_name,
    })

    return InvestigationResponse(
        id=inv.id,
        case_number=inv.case_number,
        title=inv.title,
        description=inv.description,
        status=inv.status,
        priority=inv.priority,
        assigned_to=inv.assigned_to,
        tags=inv.tags or [],
        classification=inv.classification,
        created_at=inv.created_at,
        updated_at=inv.updated_at,
    )
