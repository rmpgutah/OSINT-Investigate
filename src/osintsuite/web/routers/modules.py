"""Module discovery, correlations, and notes API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from osintsuite.config import Settings, get_settings
from osintsuite.db.repository import Repository
from osintsuite.engine.correlator import Correlator
from osintsuite.engine.investigation import InvestigationEngine
from osintsuite.web.dependencies import get_engine, get_repo

router = APIRouter()


# ── Module discovery ──────────────────────────────────────────────────

@router.get("/")
async def list_modules(engine: InvestigationEngine = Depends(get_engine)):
    """List all available OSINT modules with descriptions and target types."""
    result = []
    for name, mod in engine.modules.items():
        result.append({
            "name": name,
            "description": mod.description,
            "target_types": mod.applicable_target_types(),
        })
    return result


@router.get("/for-target/{target_id}")
async def modules_for_target(
    target_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
    engine: InvestigationEngine = Depends(get_engine),
):
    """List modules applicable to a specific target's type."""
    target = await repo.get_target(target_id)
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")

    applicable = []
    for name, mod in engine.modules.items():
        if target.target_type in mod.applicable_target_types():
            applicable.append({
                "name": name,
                "description": mod.description,
            })
    return {"target_type": target.target_type, "modules": applicable}


# ── Correlations ──────────────────────────────────────────────────────

@router.get("/correlations/{investigation_id}")
async def get_correlations(
    investigation_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
):
    """Run cross-target correlation analysis for an investigation."""
    correlator = Correlator(repo)
    correlations = await correlator.correlate_investigation(investigation_id)
    return [
        {
            "target_a": {"id": str(c.target_a_id), "label": c.target_a_label},
            "target_b": {"id": str(c.target_b_id), "label": c.target_b_label},
            "field": c.field,
            "value_a": c.value_a,
            "value_b": c.value_b,
            "match_type": c.match_type,
            "similarity": c.similarity,
        }
        for c in correlations
    ]


# ── Network Graph ─────────────────────────────────────────────────────

@router.get("/graph/{investigation_id}")
async def get_graph(
    investigation_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
):
    """Return D3-ready graph data: nodes (targets) and links (correlations)."""
    targets = await repo.list_targets(investigation_id)

    # Build nodes — all targets, even uncorrelated ones
    nodes = []
    for t in targets:
        findings = await repo.get_findings_by_target(t.id)
        nodes.append({
            "id": str(t.id),
            "label": t.label,
            "type": t.target_type,
            "findings_count": len(findings),
        })

    # Build links from correlations
    correlator = Correlator(repo)
    correlations = await correlator.correlate_investigation(investigation_id)
    links = [
        {
            "source": str(c.target_a_id),
            "target": str(c.target_b_id),
            "field": c.field,
            "match_type": c.match_type,
            "similarity": c.similarity,
            "value": c.value_a if c.value_a == c.value_b else f"{c.value_a} \u2248 {c.value_b}",
        }
        for c in correlations
    ]

    return {"nodes": nodes, "links": links}


# ── Notes ─────────────────────────────────────────────────────────────

class NoteCreate(BaseModel):
    content: str
    investigation_id: uuid.UUID | None = None
    target_id: uuid.UUID | None = None
    finding_id: uuid.UUID | None = None


@router.post("/notes")
async def create_note(data: NoteCreate, repo: Repository = Depends(get_repo)):
    """Add a note/annotation to an investigation, target, or finding."""
    note = await repo.add_note(
        content=data.content,
        investigation_id=data.investigation_id,
        target_id=data.target_id,
        finding_id=data.finding_id,
    )
    return {
        "id": str(note.id),
        "content": note.content,
        "created_at": note.created_at.isoformat(),
    }


@router.get("/notes")
async def list_notes(
    investigation_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    repo: Repository = Depends(get_repo),
):
    """List notes for an investigation or target."""
    from sqlalchemy import select
    from osintsuite.db.models import Note

    q = select(Note).order_by(Note.created_at.desc())
    if investigation_id:
        q = q.where(Note.investigation_id == investigation_id)
    if target_id:
        q = q.where(Note.target_id == target_id)

    result = await repo.session.execute(q)
    notes = result.scalars().all()
    return [
        {
            "id": str(n.id),
            "content": n.content,
            "created_at": n.created_at.isoformat(),
            "investigation_id": str(n.investigation_id) if n.investigation_id else None,
            "target_id": str(n.target_id) if n.target_id else None,
        }
        for n in notes
    ]


# ── Search ────────────────────────────────────────────────────────────

@router.get("/search")
async def global_search(
    q: str,
    repo: Repository = Depends(get_repo),
):
    """Search across investigations and targets."""
    from sqlalchemy import select, or_
    from osintsuite.db.models import Investigation, Target

    # Search investigations
    inv_q = select(Investigation).where(
        or_(
            Investigation.title.ilike(f"%{q}%"),
            Investigation.case_number.ilike(f"%{q}%"),
            Investigation.description.ilike(f"%{q}%"),
        )
    ).limit(10)
    inv_result = await repo.session.execute(inv_q)
    investigations = inv_result.scalars().all()

    # Search targets
    tgt_q = select(Target).where(
        or_(
            Target.label.ilike(f"%{q}%"),
            Target.full_name.ilike(f"%{q}%"),
            Target.email.ilike(f"%{q}%"),
            Target.phone.ilike(f"%{q}%"),
            Target.city.ilike(f"%{q}%"),
        )
    ).limit(10)
    tgt_result = await repo.session.execute(tgt_q)
    targets = tgt_result.scalars().all()

    return {
        "investigations": [
            {"id": str(i.id), "case_number": i.case_number, "title": i.title, "status": i.status}
            for i in investigations
        ],
        "targets": [
            {"id": str(t.id), "label": t.label, "target_type": t.target_type, "investigation_id": str(t.investigation_id)}
            for t in targets
        ],
    }


# ── Stats endpoint ───────────────────────────────────────────────────

@router.get("/stats/{investigation_id}")
async def get_investigation_stats(
    investigation_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
):
    """Return aggregated statistics for an investigation's findings."""
    from collections import Counter, defaultdict
    from sqlalchemy import select
    from osintsuite.db.models import Finding, Target

    # Get all targets for this investigation
    targets = await repo.list_targets(investigation_id)
    if not targets:
        return {
            "findings_by_module": {},
            "findings_by_confidence": {"high": 0, "medium": 0, "low": 0},
            "findings_by_source": {},
            "findings_over_time": [],
            "target_comparison": [],
        }

    # Gather all findings across all targets
    all_findings = []
    target_comparison = []
    for t in targets:
        findings = await repo.get_findings_by_target(t.id)
        all_findings.extend(findings)
        # Count distinct modules run for this target
        modules_seen = set()
        for f in findings:
            modules_seen.add(f.module_name)
        target_comparison.append({
            "target_label": t.label,
            "findings_count": len(findings),
            "modules_run": len(modules_seen),
        })

    # findings_by_module
    by_module = Counter()
    for f in all_findings:
        by_module[f.module_name] += 1

    # findings_by_confidence
    by_confidence = {"high": 0, "medium": 0, "low": 0}
    for f in all_findings:
        c = f.confidence or 0
        if c > 70:
            by_confidence["high"] += 1
        elif c > 40:
            by_confidence["medium"] += 1
        else:
            by_confidence["low"] += 1

    # findings_by_source
    by_source = Counter()
    for f in all_findings:
        by_source[f.source] += 1

    # findings_over_time — group by date
    by_date = Counter()
    for f in all_findings:
        day = f.created_at.strftime("%Y-%m-%d")
        by_date[day] += 1
    over_time = sorted(
        [{"date": d, "count": c} for d, c in by_date.items()],
        key=lambda x: x["date"],
    )

    return {
        "findings_by_module": dict(by_module),
        "findings_by_confidence": by_confidence,
        "findings_by_source": dict(by_source),
        "findings_over_time": over_time,
        "target_comparison": target_comparison,
    }


# ── Map data endpoint ────────────────────────────────────────────────

@router.get("/map-data/{investigation_id}")
async def get_map_data(
    investigation_id: uuid.UUID,
    repo: Repository = Depends(get_repo),
):
    """Return all findings with lat/lon data for map display."""
    targets = await repo.list_targets(investigation_id)
    geo_findings = []

    for t in targets:
        findings = await repo.get_findings_by_target(t.id)
        for f in findings:
            if not f.data or not isinstance(f.data, dict):
                continue
            lat = f.data.get("lat") or f.data.get("latitude")
            lon = f.data.get("lon") or f.data.get("longitude")
            if lat is not None and lon is not None:
                try:
                    lat_f = float(lat)
                    lon_f = float(lon)
                except (ValueError, TypeError):
                    continue
                geo_findings.append({
                    "lat": lat_f,
                    "lon": lon_f,
                    "label": f.title or f.finding_type,
                    "finding_type": f.finding_type,
                    "source": f.source,
                    "confidence": f.confidence,
                    "target_label": t.label,
                })

    return geo_findings
