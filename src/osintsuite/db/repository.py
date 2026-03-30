"""Data access layer — all CRUD operations with parameterized queries."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from osintsuite.db.models import Finding, Investigation, ModuleRun, Note, Report, Target

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
