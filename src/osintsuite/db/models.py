"""SQLAlchemy ORM models for the OSINT Investigation Suite."""

import uuid
from datetime import date, datetime
from typing import Dict, List, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator, CHAR
import uuid as _uuid_mod


class PortableUUID(TypeDecorator):
    """Platform-independent UUID type. Uses CHAR(32) for SQLite, native UUID for Postgres."""
    impl = CHAR(32)
    cache_ok = True

    def __init__(self, *args, **kwargs):
        # Accept and ignore as_uuid kwarg for Postgres compatibility
        kwargs.pop("as_uuid", None)
        super().__init__(*args, **kwargs)

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import UUID as PG_UUID
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(32))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return value
        return value.hex if isinstance(value, _uuid_mod.UUID) else value

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        if not isinstance(value, _uuid_mod.UUID):
            return _uuid_mod.UUID(value)
        return value


# Cross-database aliases
JSONB = JSON
UUID = PortableUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(20),
        CheckConstraint("status IN ('open', 'active', 'closed', 'archived')"),
        default="open",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    priority: Mapped[str] = mapped_column(
        String(10),
        CheckConstraint("priority IN ('low', 'medium', 'high', 'critical')"),
        default="medium",
        nullable=False,
    )
    assigned_to: Mapped[Optional[str]] = mapped_column(String(255))
    tags: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    classification: Mapped[str] = mapped_column(
        String(20),
        CheckConstraint("classification IN ('unclassified', 'sensitive', 'confidential', 'secret')"),
        default="unclassified",
        nullable=False,
    )

    targets: Mapped[List["Target"]] = relationship(back_populates="investigation", cascade="all, delete-orphan")
    reports: Mapped[List["Report"]] = relationship(back_populates="investigation", cascade="all, delete-orphan")
    notes: Mapped[List["Note"]] = relationship(back_populates="investigation", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Investigation {self.case_number}: {self.title}>"


class Target(Base):
    __tablename__ = "targets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(
        String(20),
        CheckConstraint(
            "target_type IN ('person', 'domain', 'email', 'phone', 'username', 'ip', 'organization')"
        ),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(255))
    email: Mapped[Optional[str]] = mapped_column(String(255))
    phone: Mapped[Optional[str]] = mapped_column(String(50))
    address: Mapped[Optional[str]] = mapped_column(Text)
    date_of_birth: Mapped[Optional[date]] = mapped_column()
    city: Mapped[Optional[str]] = mapped_column(String(100))
    state: Mapped[Optional[str]] = mapped_column(String(100))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    investigation: Mapped["Investigation"] = relationship(back_populates="targets")
    findings: Mapped[List["Finding"]] = relationship(back_populates="target", cascade="all, delete-orphan")
    module_runs: Mapped[List["ModuleRun"]] = relationship(back_populates="target", cascade="all, delete-orphan")
    notes: Mapped[List["Note"]] = relationship(back_populates="target", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_targets_investigation", "investigation_id"),
        Index("idx_targets_type", "target_type"),
    )

    def __repr__(self) -> str:
        return f"<Target {self.label} ({self.target_type})>"


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("targets.id", ondelete="CASCADE"), nullable=False
    )
    module_name: Mapped[str] = mapped_column(String(100), nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    finding_type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(500))
    content: Mapped[Optional[str]] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    confidence: Mapped[Optional[int]] = mapped_column(
        SmallInteger, CheckConstraint("confidence BETWEEN 0 AND 100")
    )
    raw_response: Mapped[Optional[str]] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False, server_default="[]")
    is_flagged: Mapped[bool] = mapped_column(default=False, nullable=False)
    is_reviewed: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    target: Mapped["Target"] = relationship(back_populates="findings")
    notes: Mapped[List["Note"]] = relationship(back_populates="finding", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_findings_target", "target_id"),
        Index("idx_findings_module", "module_name"),
    )

    def __repr__(self) -> str:
        return f"<Finding {self.module_name}/{self.source}: {self.title}>"


class ModuleRun(Base):
    __tablename__ = "module_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("targets.id", ondelete="CASCADE"), nullable=False
    )
    module_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        CheckConstraint("status IN ('pending', 'running', 'completed', 'failed')"),
        default="pending",
        nullable=False,
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    findings_count: Mapped[int] = mapped_column(Integer, default=0)

    target: Mapped["Target"] = relationship(back_populates="module_runs")

    __table_args__ = (Index("idx_module_runs_target", "target_id"),)

    def __repr__(self) -> str:
        return f"<ModuleRun {self.module_name} [{self.status}]>"


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    format: Mapped[str] = mapped_column(
        String(10), CheckConstraint("format IN ('csv', 'html', 'pdf', 'json')"), nullable=False
    )
    file_path: Mapped[Optional[str]] = mapped_column(String(500))
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    investigation: Mapped["Investigation"] = relationship(back_populates="reports")

    def __repr__(self) -> str:
        return f"<Report {self.title} ({self.format})>"


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    investigation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("investigations.id", ondelete="CASCADE")
    )
    target_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("targets.id", ondelete="CASCADE")
    )
    finding_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE")
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    investigation: Mapped[Optional["Investigation"]] = relationship(back_populates="notes")
    target: Mapped[Optional["Target"]] = relationship(back_populates="notes")
    finding: Mapped[Optional["Finding"]] = relationship(back_populates="notes")

    def __repr__(self) -> str:
        return f"<Note {self.id} ({len(self.content)} chars)>"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(
        String(50),
        CheckConstraint("entity_type IN ('investigation', 'target', 'finding')"),
        nullable=False,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(
        String(50),
        CheckConstraint("action IN ('created', 'updated', 'deleted', 'module_run', 'status_change')"),
        nullable=False,
    )
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("idx_audit_entity", "entity_type", "entity_id"),
        Index("idx_audit_created", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<AuditLog {self.action} on {self.entity_type}/{self.entity_id}>"


class FindingLink(Base):
    __tablename__ = "finding_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    finding_a_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), nullable=False
    )
    finding_b_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("findings.id", ondelete="CASCADE"), nullable=False
    )
    relationship_type: Mapped[str] = mapped_column("relationship", String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    finding_a: Mapped["Finding"] = relationship(foreign_keys=[finding_a_id])
    finding_b: Mapped["Finding"] = relationship(foreign_keys=[finding_b_id])

    __table_args__ = (
        Index("idx_finding_links_a", "finding_a_id"),
        Index("idx_finding_links_b", "finding_b_id"),
    )

    def __repr__(self) -> str:
        return f"<FindingLink {self.finding_a_id} --{self.relationship_type}--> {self.finding_b_id}>"
