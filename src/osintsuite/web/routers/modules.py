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
