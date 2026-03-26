"""PDF report format — converts HTML report to PDF via weasyprint."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from osintsuite.reporting.formats.html_report import HTMLReport

if TYPE_CHECKING:
    from osintsuite.db.models import Investigation


class PDFReport:
    def __init__(self):
        self._html_renderer = HTMLReport()

    def render(self, investigation: Investigation, output_path: Path) -> None:
        from weasyprint import HTML

        html_content = self._html_renderer._build_html(investigation)
        HTML(string=html_content).write_pdf(str(output_path))
