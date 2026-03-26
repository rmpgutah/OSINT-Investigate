"""Report generator — orchestrates report creation across formats."""

from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

from osintsuite.config import Settings
from osintsuite.db.repository import Repository
from osintsuite.reporting.formats.csv_report import CSVReport
from osintsuite.reporting.formats.html_report import HTMLReport
from osintsuite.reporting.formats.json_report import JSONReport

logger = logging.getLogger(__name__)


class ReportGenerator:
    def __init__(self, db: Repository, settings: Settings):
        self.db = db
        self.settings = settings
        self.formats = {
            "csv": CSVReport(),
            "html": HTMLReport(),
            "json": JSONReport(),
        }

        # PDF requires weasyprint which may not be installed
        try:
            from osintsuite.reporting.formats.pdf_report import PDFReport
            self.formats["pdf"] = PDFReport()
        except ImportError:
            logger.warning("weasyprint not available — PDF reports disabled")

    async def generate(self, investigation_id: UUID, format: str) -> Path:
        """Generate a report in the specified format."""
        if format not in self.formats:
            raise ValueError(f"Unsupported format: {format}. Available: {list(self.formats.keys())}")

        inv = await self.db.get_investigation_full(investigation_id)
        if not inv:
            raise ValueError(f"Investigation {investigation_id} not found")

        # Ensure reports directory exists
        reports_dir = Path(self.settings.reports_dir)
        reports_dir.mkdir(parents=True, exist_ok=True)

        output_path = reports_dir / f"{inv.case_number}_report.{format}"

        formatter = self.formats[format]
        formatter.render(inv, output_path)

        # Save report record
        await self.db.save_report(
            investigation_id=investigation_id,
            title=f"{inv.case_number} — {inv.title}",
            format=format,
            file_path=str(output_path),
        )

        logger.info(f"Report generated: {output_path}")
        return output_path
