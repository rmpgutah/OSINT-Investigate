"""Pydantic request/response models for the web API."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


# ── Requests ────────────────────────────────────────────────────

class InvestigationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None


class TargetCreate(BaseModel):
    investigation_id: uuid.UUID
    target_type: str = Field(pattern=r"^(person|domain|email|phone|username|ip|organization)$")
    label: str = Field(min_length=1, max_length=255)
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    date_of_birth: date | None = None
    city: str | None = None
    state: str | None = None
    metadata_: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class ModuleRunRequest(BaseModel):
    module_name: str | None = None  # None = run all applicable


class ReportRequest(BaseModel):
    format: str = Field(pattern=r"^(csv|html|json|pdf)$", default="html")


class NoteCreate(BaseModel):
    content: str = Field(min_length=1)
    investigation_id: uuid.UUID | None = None
    target_id: uuid.UUID | None = None
    finding_id: uuid.UUID | None = None


class InvestigationUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: str | None = Field(default=None, pattern=r"^(low|medium|high|critical)$")
    assigned_to: str | None = None
    tags: list[str] | None = None
    classification: str | None = Field(
        default=None, pattern=r"^(unclassified|sensitive|confidential|secret)$"
    )


class TargetUpdate(BaseModel):
    label: str | None = None
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    state: str | None = None


class FindingUpdate(BaseModel):
    is_flagged: bool | None = None
    is_reviewed: bool | None = None
    confidence: int | None = None


# ── Responses ───────────────────────────────────────────────────

class InvestigationResponse(BaseModel):
    id: uuid.UUID
    case_number: str
    title: str
    description: str | None
    status: str
    priority: str = "medium"
    assigned_to: str | None = None
    tags: list[str] = []
    classification: str = "unclassified"
    created_at: datetime
    updated_at: datetime
    target_count: int = 0
    finding_count: int = 0

    model_config = {"from_attributes": True}


class TargetResponse(BaseModel):
    id: uuid.UUID
    investigation_id: uuid.UUID
    target_type: str
    label: str
    full_name: str | None
    email: str | None
    phone: str | None
    address: str | None
    date_of_birth: date | None
    city: str | None
    state: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class FindingResponse(BaseModel):
    id: uuid.UUID
    target_id: uuid.UUID
    module_name: str
    source: str
    finding_type: str
    title: str | None
    content: str | None
    data: dict[str, Any]
    confidence: int | None
    is_flagged: bool = False
    is_reviewed: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class ReportResponse(BaseModel):
    id: uuid.UUID
    investigation_id: uuid.UUID
    title: str
    format: str
    file_path: str | None
    generated_at: datetime

    model_config = {"from_attributes": True}


# ── Workflow Schemas ─────────────────────────────────────────────

class FindingLinkRequest(BaseModel):
    finding_a_id: uuid.UUID
    finding_b_id: uuid.UUID
    relationship: str = Field(min_length=1, max_length=100)


class FindingLinkResponse(BaseModel):
    id: uuid.UUID
    finding_a_id: uuid.UUID
    finding_b_id: uuid.UUID
    relationship: str
    created_at: datetime

    model_config = {"from_attributes": True}


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    action: str
    details: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class TimelineEvent(BaseModel):
    type: str
    timestamp: str | None = None
    action: str | None = None
    details: dict[str, Any] | None = None
    module_name: str | None = None
    status: str | None = None
    findings_count: int | None = None
    title: str | None = None
    source: str | None = None


class CaseTemplate(BaseModel):
    name: str
    description: str
    target_types: list[str]


class FromTemplateRequest(BaseModel):
    template_name: str
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
