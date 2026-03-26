"""CSV report format — safe export using Python csv module (no injection)."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from osintsuite.db.models import Investigation


class CSVReport:
    def render(self, investigation: Investigation, output_path: Path) -> None:
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL)

            # Header
            writer.writerow([
                "Target Label",
                "Target Type",
                "Target Name",
                "Target Email",
                "Target Phone",
                "Target City",
                "Target State",
                "Module",
                "Source",
                "Finding Type",
                "Finding Title",
                "Finding Content",
                "Confidence",
                "Date",
            ])

            # Data rows
            for target in investigation.targets:
                for finding in target.findings:
                    # Sanitize values to prevent CSV formula injection
                    writer.writerow([
                        self._sanitize(target.label),
                        target.target_type,
                        self._sanitize(target.full_name or ""),
                        self._sanitize(target.email or ""),
                        self._sanitize(target.phone or ""),
                        self._sanitize(target.city or ""),
                        self._sanitize(target.state or ""),
                        finding.module_name,
                        finding.source,
                        finding.finding_type,
                        self._sanitize(finding.title or ""),
                        self._sanitize((finding.content or "")[:500]),
                        str(finding.confidence or ""),
                        finding.created_at.strftime("%Y-%m-%d %H:%M"),
                    ])

    @staticmethod
    def _sanitize(value: str) -> str:
        """Prevent CSV formula injection by prefixing dangerous characters."""
        if value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
            return f"'{value}"
        return value
