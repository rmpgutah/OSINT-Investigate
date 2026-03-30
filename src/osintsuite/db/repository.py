"""Data access layer — all CRUD operations with parameterized queries."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from osintsuite.db.models import AuditLog, Finding, FindingLink, Investigation, ModuleRun, Note, Report, Target

if TYPE_CHECKING:
    from osintsuite.modules.base import ModuleResult


class Repository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── Investigations ──────────────────────────────────────────────

    async def create_investigation(
        self, title: str, description: str | None = None
    ) -> Investigation:
        count = (await self.session.execute(select(func.count(Investigation.id)))).scalar_one()
        case_number = f"CASE-{count + 1:04d}"
        inv = Investigation(title=title, description=description, case_number=case_number)
        self.session.add(inv)
        await self.session.flush()
        return inv

    async def get_investigation(self, investigation_id: uuid.UUID) -> Investigation | None:
        return await self.session.get(Investigation, investigation_id)

    async def get_investigation_by_case(self, case_number: str) -> Investigation | None:
        result = await self.session.execute(
            select(Investigation).where(Investigation.case_number == case_number)
        )
        return result.scalar_one_or_none()

    async def list_investigations(self, status: str | None = None) -> Sequence[Investigation]:
        stmt = select(Investigation).order_by(Investigation.created_at.desc())
        if status:
            stmt = stmt.where(Investigation.status == status)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_investigation_status(
        self, investigation_id: uuid.UUID, status: str
    ) -> None:
        values = {"status": status, "updated_at": datetime.now(timezone.utc)}
        if status == "closed":
            values["closed_at"] = datetime.now(timezone.utc)
        await self.session.execute(
            update(Investigation).where(Investigation.id == investigation_id).values(**values)
        )

    async def update_investigation(
        self, investigation_id: uuid.UUID, **fields
    ) -> None:
        fields["updated_at"] = datetime.now(timezone.utc)
        await self.session.execute(
            update(Investigation).where(Investigation.id == investigation_id).values(**fields)
        )

    async def delete_investigation(self, investigation_id: uuid.UUID) -> None:
        await self.session.execute(
            delete(Investigation).where(Investigation.id == investigation_id)
        )

    # ── Targets ─────────────────────────────────────────────────────

    async def add_target(
        self,
        investigation_id: uuid.UUID,
        target_type: str,
        label: str,
        **fields,
    ) -> Target:
        target = Target(
            investigation_id=investigation_id,
            target_type=target_type,
            label=label,
            **fields,
        )
        self.session.add(target)
        await self.session.flush()
        return target

    async def get_target(self, target_id: uuid.UUID) -> Target | None:
        return await self.session.get(Target, target_id)

    async def list_targets(
        self, investigation_id: uuid.UUID | None = None
    ) -> Sequence[Target]:
        stmt = select(Target).order_by(Target.created_at.desc())
        if investigation_id:
            stmt = stmt.where(Target.investigation_id == investigation_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def search_targets(self, query: str) -> Sequence[Target]:
        pattern = f"%{query}%"
        stmt = select(Target).where(
            Target.label.ilike(pattern)
            | Target.full_name.ilike(pattern)
            | Target.email.ilike(pattern)
            | Target.phone.ilike(pattern)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def update_target(self, target_id: uuid.UUID, **fields) -> None:
        fields["updated_at"] = datetime.now(timezone.utc)
        await self.session.execute(
            update(Target).where(Target.id == target_id).values(**fields)
        )

    async def delete_target(self, target_id: uuid.UUID) -> None:
        await self.session.execute(delete(Target).where(Target.id == target_id))

    # ── Findings ────────────────────────────────────────────────────

    async def save_findings(
        self, target_id: uuid.UUID, results: list[ModuleResult]
    ) -> list[Finding]:
        findings = []
        for r in results:
            f = Finding(
                target_id=target_id,
                module_name=r.module_name,
                source=r.source,
                finding_type=r.finding_type,
                title=r.title,
                content=r.content,
                data=r.data,
                confidence=r.confidence,
                raw_response=r.raw_response,
            )
            self.session.add(f)
            findings.append(f)
        await self.session.flush()
        return findings

    async def get_findings_by_target(
        self, target_id: uuid.UUID, module_name: str | None = None
    ) -> Sequence[Finding]:
        stmt = select(Finding).where(Finding.target_id == target_id)
        if module_name:
            stmt = stmt.where(Finding.module_name == module_name)
        stmt = stmt.order_by(Finding.created_at.desc())
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_finding(self, finding_id: uuid.UUID) -> Finding | None:
        return await self.session.get(Finding, finding_id)

    async def update_finding(self, finding_id: uuid.UUID, **fields) -> None:
        await self.session.execute(
            update(Finding).where(Finding.id == finding_id).values(**fields)
        )

    async def delete_finding(self, finding_id: uuid.UUID) -> None:
        await self.session.execute(delete(Finding).where(Finding.id == finding_id))

    # ── Module Runs ─────────────────────────────────────────────────

    async def create_module_run(
        self, target_id: uuid.UUID, module_name: str
    ) -> ModuleRun:
        run = ModuleRun(
            target_id=target_id,
            module_name=module_name,
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def complete_module_run(self, run_id: uuid.UUID, findings_count: int) -> None:
        await self.session.execute(
            update(ModuleRun)
            .where(ModuleRun.id == run_id)
            .values(
                status="completed",
                completed_at=datetime.now(timezone.utc),
                findings_count=findings_count,
            )
        )

    async def fail_module_run(self, run_id: uuid.UUID, error: str) -> None:
        await self.session.execute(
            update(ModuleRun)
            .where(ModuleRun.id == run_id)
            .values(
                status="failed",
                completed_at=datetime.now(timezone.utc),
                error_message=error,
            )
        )

    # ── Reports ─────────────────────────────────────────────────────

    async def save_report(
        self, investigation_id: uuid.UUID, title: str, format: str, file_path: str
    ) -> Report:
        report = Report(
            investigation_id=investigation_id,
            title=title,
            format=format,
            file_path=file_path,
        )
        self.session.add(report)
        await self.session.flush()
        return report

    async def list_reports(self, investigation_id: uuid.UUID) -> Sequence[Report]:
        stmt = (
            select(Report)
            .where(Report.investigation_id == investigation_id)
            .order_by(Report.generated_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    # ── Notes ───────────────────────────────────────────────────────

    async def add_note(
        self,
        content: str,
        investigation_id: uuid.UUID | None = None,
        target_id: uuid.UUID | None = None,
        finding_id: uuid.UUID | None = None,
    ) -> Note:
        note = Note(
            content=content,
            investigation_id=investigation_id,
            target_id=target_id,
            finding_id=finding_id,
        )
        self.session.add(note)
        await self.session.flush()
        return note

    # ── Investigation Summary ───────────────────────────────────────

    async def get_investigation_full(self, investigation_id: uuid.UUID) -> Investigation | None:
        stmt = (
            select(Investigation)
            .where(Investigation.id == investigation_id)
            .options(
                selectinload(Investigation.targets).selectinload(Target.findings),
                selectinload(Investigation.targets).selectinload(Target.module_runs),
                selectinload(Investigation.reports),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # ── Audit Log ──────────────────────────────────────────────────

    async def log_audit(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        action: str,
        details: dict | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            details=details or {},
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def get_audit_log(
        self,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> Sequence[AuditLog]:
        stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
        if entity_type:
            stmt = stmt.where(AuditLog.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(AuditLog.entity_id == entity_id)
        result = await self.session.execute(stmt)
        return result.scalars().all()

    # ── Clone Investigation ────────────────────────────────────────

    async def clone_investigation(self, investigation_id: uuid.UUID) -> Investigation:
        original = await self.get_investigation_full(investigation_id)
        if not original:
            raise ValueError("Investigation not found")

        # Create new investigation with copied metadata
        new_inv = await self.create_investigation(
            title=f"{original.title} (Clone)",
            description=original.description,
        )
        # Copy new workflow fields
        await self.update_investigation(
            new_inv.id,
            priority=original.priority,
            assigned_to=original.assigned_to,
            tags=list(original.tags) if original.tags else [],
            classification=original.classification,
        )

        # Clone targets (structure only, no findings)
        for target in original.targets:
            await self.add_target(
                investigation_id=new_inv.id,
                target_type=target.target_type,
                label=target.label,
                full_name=target.full_name,
                email=target.email,
                phone=target.phone,
                address=target.address,
                date_of_birth=target.date_of_birth,
                city=target.city,
                state=target.state,
                metadata_=dict(target.metadata_) if target.metadata_ else {},
            )

        await self.log_audit("investigation", new_inv.id, "created", {
            "cloned_from": str(investigation_id),
        })
        return new_inv

    # ── Investigation Timeline ─────────────────────────────────────

    async def get_investigation_timeline(
        self, investigation_id: uuid.UUID
    ) -> list[dict]:
        """Get all audit log entries, module runs, and finding creation dates sorted by time."""
        events: list[dict] = []

        # Audit log entries for the investigation itself
        audit_entries = await self.get_audit_log(
            entity_type="investigation", entity_id=investigation_id, limit=500
        )
        for entry in audit_entries:
            events.append({
                "type": "audit",
                "action": entry.action,
                "details": entry.details,
                "timestamp": entry.created_at.isoformat() if entry.created_at else None,
            })

        # Get targets for this investigation
        targets = await self.list_targets(investigation_id)
        target_ids = [t.id for t in targets]

        if target_ids:
            # Module runs
            stmt = (
                select(ModuleRun)
                .where(ModuleRun.target_id.in_(target_ids))
                .order_by(ModuleRun.started_at.desc())
            )
            result = await self.session.execute(stmt)
            for run in result.scalars().all():
                events.append({
                    "type": "module_run",
                    "module_name": run.module_name,
                    "status": run.status,
                    "findings_count": run.findings_count,
                    "timestamp": (run.started_at or run.completed_at).isoformat() if (run.started_at or run.completed_at) else None,
                })

            # Finding creation dates
            stmt = (
                select(Finding)
                .where(Finding.target_id.in_(target_ids))
                .order_by(Finding.created_at.desc())
            )
            result = await self.session.execute(stmt)
            for finding in result.scalars().all():
                events.append({
                    "type": "finding_created",
                    "title": finding.title,
                    "module_name": finding.module_name,
                    "source": finding.source,
                    "timestamp": finding.created_at.isoformat() if finding.created_at else None,
                })

        # Sort all events by timestamp descending
        events.sort(key=lambda e: e.get("timestamp") or "", reverse=True)
        return events

    # ── Finding Links ──────────────────────────────────────────────

    async def link_findings(
        self,
        finding_a_id: uuid.UUID,
        finding_b_id: uuid.UUID,
        relationship: str,
    ) -> FindingLink:
        link = FindingLink(
            finding_a_id=finding_a_id,
            finding_b_id=finding_b_id,
            relationship_type=relationship,
        )
        self.session.add(link)
        await self.session.flush()
        return link

    async def get_finding_links(self, finding_id: uuid.UUID) -> Sequence[FindingLink]:
        stmt = select(FindingLink).where(
            (FindingLink.finding_a_id == finding_id) | (FindingLink.finding_b_id == finding_id)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    # ── Deduplication ──────────────────────────────────────────────

    async def get_investigation_stats(self) -> dict:
        """Global investigation statistics."""
        inv_count = await self.session.scalar(select(func.count(Investigation.id)))
        target_count = await self.session.scalar(select(func.count(Target.id)))
        finding_count = await self.session.scalar(select(func.count(Finding.id)))
        run_count = await self.session.scalar(select(func.count(ModuleRun.id)))

        # By status
        status_counts = {}
        for status in ['open', 'active', 'closed', 'archived']:
            c = await self.session.scalar(
                select(func.count(Investigation.id)).where(Investigation.status == status)
            )
            status_counts[status] = c or 0

        return {
            "total_investigations": inv_count or 0,
            "total_targets": target_count or 0,
            "total_findings": finding_count or 0,
            "total_module_runs": run_count or 0,
            "by_status": status_counts,
            "avg_findings_per_case": round((finding_count or 0) / max(inv_count or 1, 1), 1),
        }

    async def get_finding_stats(self, target_id: uuid.UUID) -> dict:
        """Finding statistics for a target."""
        findings = await self.get_findings_by_target(target_id)
        by_module: dict[str, int] = {}
        high = med = low = flagged = reviewed = 0
        for f in findings:
            by_module[f.module_name] = by_module.get(f.module_name, 0) + 1
            c = f.confidence or 0
            if c > 70:
                high += 1
            elif c > 40:
                med += 1
            else:
                low += 1
            if f.is_flagged:
                flagged += 1
            if f.is_reviewed:
                reviewed += 1

        return {
            "total": len(findings),
            "by_module": by_module,
            "confidence": {"high": high, "medium": med, "low": low},
            "flagged": flagged,
            "reviewed": reviewed,
        }

    async def bulk_update_findings(self, finding_ids: list, **fields) -> int:
        """Update multiple findings at once."""
        stmt = update(Finding).where(Finding.id.in_(finding_ids)).values(**fields)
        result = await self.session.execute(stmt)
        await self.session.commit()
        return result.rowcount

    async def deduplicate_findings(self, target_id: uuid.UUID) -> dict:
        """Find findings with same title+source, keep highest confidence, delete rest."""
        findings = await self.get_findings_by_target(target_id)

        # Group by (title, source)
        groups: dict[tuple, list[Finding]] = {}
        for f in findings:
            key = (f.title, f.source)
            groups.setdefault(key, []).append(f)

        removed = 0
        kept = 0
        for key, group in groups.items():
            if len(group) <= 1:
                kept += len(group)
                continue
            # Sort by confidence descending (None treated as -1)
            group.sort(key=lambda x: x.confidence if x.confidence is not None else -1, reverse=True)
            kept += 1
            for dup in group[1:]:
                await self.delete_finding(dup.id)
                removed += 1

        return {"kept": kept, "removed": removed}
