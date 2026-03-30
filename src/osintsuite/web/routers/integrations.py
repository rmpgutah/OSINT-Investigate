"""Integration API endpoints: webhooks, scheduled runs, MISP/STIX export, bulk import."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from osintsuite.db.repository import Repository
from osintsuite.engine.investigation import InvestigationEngine
from osintsuite.web.dependencies import get_engine, get_repo

router = APIRouter()
logger = logging.getLogger(__name__)


# ============================================================
# 81. Webhook Notifications
# ============================================================

class WebhookRegistration(BaseModel):
    url: str = Field(min_length=1, description="URL to POST findings to")
    events: list[str] = Field(
        default=["module_complete"],
        description="Event types to subscribe to",
    )


class WebhookManager:
    """Simple in-memory webhook store. POSTs findings to registered URLs."""

    _instance = None

    def __init__(self):
        self.webhooks: dict[str, dict[str, Any]] = {}
        self._http = httpx.AsyncClient(timeout=10)

    @classmethod
    def get(cls) -> "WebhookManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, url: str, events: list[str]) -> str:
        hook_id = str(uuid.uuid4())[:8]
        self.webhooks[hook_id] = {
            "id": hook_id,
            "url": url,
            "events": events,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        return hook_id

    def unregister(self, hook_id: str) -> bool:
        return self.webhooks.pop(hook_id, None) is not None

    def list_all(self) -> list[dict]:
        return list(self.webhooks.values())

    async def notify(self, event: str, payload: dict):
        """POST payload to all webhooks subscribed to this event."""
        for hook in self.webhooks.values():
            if event in hook["events"]:
                try:
                    await self._http.post(
                        hook["url"],
                        json={"event": event, "data": payload},
                        headers={"Content-Type": "application/json"},
                    )
                except Exception as e:
                    logger.warning(f"Webhook {hook['id']} delivery failed: {e}")


@router.post("/webhooks")
async def register_webhook(data: WebhookRegistration):
    """Register a webhook URL to receive notifications."""
    mgr = WebhookManager.get()
    hook_id = mgr.register(data.url, data.events)
    return {"id": hook_id, "url": data.url, "events": data.events}


@router.get("/webhooks")
async def list_webhooks():
    """List all registered webhooks."""
    return WebhookManager.get().list_all()


@router.delete("/webhooks/{hook_id}")
async def delete_webhook(hook_id: str):
    """Remove a registered webhook."""
    if not WebhookManager.get().unregister(hook_id):
        raise HTTPException(status_code=404, detail="Webhook not found")
    return {"status": "deleted"}


# ============================================================
# 84. Scheduled Module Runs
# ============================================================

class ScheduleRequest(BaseModel):
    target_id: uuid.UUID
    interval_hours: float = Field(gt=0, le=168, description="Re-run interval in hours")
    module_name: str | None = None  # None = all applicable


_schedules: dict[str, dict] = {}
_schedule_tasks: dict[str, asyncio.Task] = {}


async def _scheduled_runner(
    schedule_id: str,
    target_id: uuid.UUID,
    interval_hours: float,
    module_name: str | None,
):
    """Background task that re-runs modules on a timer."""
    from osintsuite.config import get_settings
    from osintsuite.db.session import get_async_session_factory

    settings = get_settings()
    factory = get_async_session_factory(settings)

    while True:
        await asyncio.sleep(interval_hours * 3600)
        try:
            async with factory() as session:
                repo = Repository(session)
                engine = InvestigationEngine(repo, settings)
                if module_name:
                    findings = await engine.run_module(target_id, module_name)
                    count = len(findings)
                else:
                    results = await engine.run_all_applicable(target_id)
                    count = sum(len(f) for f in results.values())
                await session.commit()

                # Notify webhooks
                await WebhookManager.get().notify("module_complete", {
                    "schedule_id": schedule_id,
                    "target_id": str(target_id),
                    "findings_count": count,
                })

                _schedules[schedule_id]["last_run"] = datetime.now(timezone.utc).isoformat()
                _schedules[schedule_id]["run_count"] = _schedules[schedule_id].get("run_count", 0) + 1
                logger.info(f"Scheduled run {schedule_id}: {count} findings")
        except Exception as e:
            logger.error(f"Scheduled run {schedule_id} failed: {e}")


@router.post("/schedule")
async def create_schedule(data: ScheduleRequest):
    """Schedule periodic module runs for a target."""
    schedule_id = str(uuid.uuid4())[:8]
    _schedules[schedule_id] = {
        "id": schedule_id,
        "target_id": str(data.target_id),
        "interval_hours": data.interval_hours,
        "module_name": data.module_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_run": None,
        "run_count": 0,
        "active": True,
    }
    task = asyncio.create_task(
        _scheduled_runner(schedule_id, data.target_id, data.interval_hours, data.module_name)
    )
    _schedule_tasks[schedule_id] = task
    return _schedules[schedule_id]


@router.get("/schedules")
async def list_schedules():
    """List all active schedules."""
    return list(_schedules.values())


@router.delete("/schedule/{schedule_id}")
async def cancel_schedule(schedule_id: str):
    """Cancel a scheduled run."""
    task = _schedule_tasks.pop(schedule_id, None)
    if task:
        task.cancel()
    info = _schedules.pop(schedule_id, None)
    if not info:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return {"status": "cancelled", "id": schedule_id}


# ============================================================
# 87. MISP Export
# ============================================================

@router.get("/misp-export/{investigation_id}")
async def misp_export(
    investigation_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
):
    """Export findings as a MISP event JSON."""
    inv = await repo.get_investigation_full(investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")

    attributes = []
    for target in inv.targets:
        findings = await repo.get_findings_by_target(target.id)
        for f in findings:
            # Map finding_type to MISP attribute type
            misp_type = _finding_type_to_misp(f.finding_type, f.source)
            attr = {
                "type": misp_type,
                "category": _finding_source_to_category(f.source),
                "value": f.content or f.title or "",
                "comment": f.title or "",
                "to_ids": f.confidence is not None and f.confidence > 60,
                "timestamp": int(f.created_at.timestamp()) if f.created_at else 0,
                "Tag": [],
            }
            if f.is_flagged:
                attr["Tag"].append({"name": "osint:flagged"})
            if f.module_name:
                attr["Tag"].append({"name": f"osint:module={f.module_name}"})
            attributes.append(attr)

    misp_event = {
        "Event": {
            "info": f"{inv.case_number}: {inv.title}",
            "date": inv.created_at.strftime("%Y-%m-%d") if inv.created_at else "",
            "threat_level_id": "2",
            "analysis": "1" if inv.status == "active" else "2" if inv.status == "closed" else "0",
            "distribution": "0",
            "published": False,
            "Attribute": attributes,
            "Tag": [
                {"name": f"osint:case={inv.case_number}"},
                {"name": f"osint:status={inv.status}"},
            ],
        }
    }
    return JSONResponse(content=misp_event)


def _finding_type_to_misp(finding_type: str, source: str) -> str:
    """Map our finding types to MISP attribute types."""
    mapping = {
        "email": "email-src",
        "domain": "domain",
        "ip": "ip-dst",
        "url": "url",
        "phone": "phone-number",
        "username": "github-username",
        "hash": "md5",
        "filename": "filename",
        "person": "text",
    }
    return mapping.get(finding_type, "text")


def _finding_source_to_category(source: str) -> str:
    """Map source to MISP category."""
    source_lower = source.lower()
    if any(x in source_lower for x in ["email", "breach"]):
        return "Network activity"
    if any(x in source_lower for x in ["social", "username"]):
        return "Social network"
    if any(x in source_lower for x in ["domain", "dns", "ip", "whois"]):
        return "Network activity"
    if any(x in source_lower for x in ["person", "record", "court"]):
        return "Person"
    return "External analysis"


# ============================================================
# 88. STIX 2.1 Export
# ============================================================

@router.get("/stix-export/{investigation_id}")
async def stix_export(
    investigation_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
):
    """Export findings as a STIX 2.1 bundle JSON."""
    inv = await repo.get_investigation_full(investigation_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")

    objects = []

    # Identity for the investigation
    identity_id = f"identity--{inv.id}"
    objects.append({
        "type": "identity",
        "spec_version": "2.1",
        "id": identity_id,
        "created": inv.created_at.isoformat() if inv.created_at else "",
        "modified": inv.updated_at.isoformat() if inv.updated_at else "",
        "name": f"{inv.case_number}: {inv.title}",
        "identity_class": "organization",
    })

    # Report object
    report_id = f"report--{inv.id}"
    indicator_refs = []

    for target in inv.targets:
        findings = await repo.get_findings_by_target(target.id)
        for f in findings:
            stix_type, stix_obj = _finding_to_stix_object(f)
            if stix_obj:
                objects.append(stix_obj)
                indicator_refs.append(stix_obj["id"])

                # Add relationship to identity
                rel_id = f"relationship--{f.id}"
                objects.append({
                    "type": "relationship",
                    "spec_version": "2.1",
                    "id": rel_id,
                    "created": f.created_at.isoformat() if f.created_at else "",
                    "modified": f.created_at.isoformat() if f.created_at else "",
                    "relationship_type": "related-to",
                    "source_ref": stix_obj["id"],
                    "target_ref": identity_id,
                })

    # Add the report
    objects.append({
        "type": "report",
        "spec_version": "2.1",
        "id": report_id,
        "created": inv.created_at.isoformat() if inv.created_at else "",
        "modified": inv.updated_at.isoformat() if inv.updated_at else "",
        "name": f"{inv.case_number}: {inv.title}",
        "published": inv.created_at.isoformat() if inv.created_at else "",
        "object_refs": indicator_refs,
    })

    bundle = {
        "type": "bundle",
        "id": f"bundle--{uuid.uuid4()}",
        "objects": objects,
    }
    return JSONResponse(content=bundle)


def _finding_to_stix_object(finding) -> tuple[str, dict | None]:
    """Convert a Finding to a STIX 2.1 observed-data or indicator object."""
    value = finding.content or finding.title or ""
    if not value:
        return "", None

    stix_id = f"indicator--{finding.id}"
    pattern = _make_stix_pattern(finding.finding_type, value)

    obj = {
        "type": "indicator",
        "spec_version": "2.1",
        "id": stix_id,
        "created": finding.created_at.isoformat() if finding.created_at else "",
        "modified": finding.created_at.isoformat() if finding.created_at else "",
        "name": finding.title or finding.finding_type,
        "description": f"Source: {finding.source}, Module: {finding.module_name}",
        "pattern": pattern,
        "pattern_type": "stix",
        "valid_from": finding.created_at.isoformat() if finding.created_at else "",
        "confidence": finding.confidence or 0,
        "labels": [finding.module_name, finding.source],
    }
    return "indicator", obj


def _make_stix_pattern(finding_type: str, value: str) -> str:
    """Create a STIX pattern from finding type and value."""
    escaped = value.replace("'", "\\'")
    mapping = {
        "email": f"[email-addr:value = '{escaped}']",
        "domain": f"[domain-name:value = '{escaped}']",
        "ip": f"[ipv4-addr:value = '{escaped}']",
        "url": f"[url:value = '{escaped}']",
        "hash": f"[file:hashes.MD5 = '{escaped}']",
    }
    return mapping.get(finding_type, f"[artifact:payload_bin = '{escaped}']")


# ============================================================
# 90. Bulk Import
# ============================================================

@router.post("/bulk-import")
async def bulk_import(
    request: Request,
    repo: Repository = Depends(get_repo),
):
    """Import targets from CSV body. Columns: target_type,label,full_name,email,phone,city,state,investigation_id"""
    body = await request.body()
    text = body.decode("utf-8")

    reader = csv.DictReader(io.StringIO(text))
    created = []
    errors = []

    for i, row in enumerate(reader, start=1):
        try:
            inv_id = row.get("investigation_id", "").strip()
            if not inv_id:
                errors.append({"row": i, "error": "Missing investigation_id"})
                continue

            target = await repo.add_target(
                investigation_id=uuid.UUID(inv_id),
                target_type=row.get("target_type", "person").strip(),
                label=row.get("label", "").strip() or f"Import-{i}",
                full_name=row.get("full_name", "").strip() or None,
                email=row.get("email", "").strip() or None,
                phone=row.get("phone", "").strip() or None,
                city=row.get("city", "").strip() or None,
                state=row.get("state", "").strip() or None,
            )
            created.append({
                "id": str(target.id),
                "label": target.label,
                "target_type": target.target_type,
            })
        except Exception as e:
            errors.append({"row": i, "error": str(e)})

    return {
        "imported": len(created),
        "errors": len(errors),
        "targets": created,
        "error_details": errors,
    }
