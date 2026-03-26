"""HTML report format — self-contained standalone HTML file."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from osintsuite.db.models import Investigation


class HTMLReport:
    def render(self, investigation: Investigation, output_path: Path) -> None:
        html = self._build_html(investigation)
        output_path.write_text(html, encoding="utf-8")

    def _build_html(self, inv: Investigation) -> str:
        targets_html = ""
        total_findings = 0

        for target in inv.targets:
            findings_rows = ""
            for f in target.findings:
                total_findings += 1
                conf_class = "high" if f.confidence and f.confidence > 70 else "medium" if f.confidence and f.confidence > 40 else "low"
                content_display = escape(f.content[:200] if f.content else "")
                if f.content and f.content.startswith("http"):
                    content_display = f'<a href="{escape(f.content)}">{escape(f.content[:80])}</a>'

                findings_rows += f"""
                <tr>
                    <td>{escape(f.module_name)}</td>
                    <td>{escape(f.source)}</td>
                    <td>{escape(f.finding_type)}</td>
                    <td>{escape(f.title or '')}</td>
                    <td>{content_display}</td>
                    <td class="confidence-{conf_class}">{f.confidence or '—'}%</td>
                </tr>"""

            targets_html += f"""
            <div class="target-section">
                <h3>{escape(target.label)} <span class="badge">{escape(target.target_type)}</span></h3>
                <div class="target-info">
                    {'<p><strong>Name:</strong> ' + escape(target.full_name) + '</p>' if target.full_name else ''}
                    {'<p><strong>Email:</strong> ' + escape(target.email) + '</p>' if target.email else ''}
                    {'<p><strong>Phone:</strong> ' + escape(target.phone) + '</p>' if target.phone else ''}
                    {'<p><strong>Location:</strong> ' + escape(target.city or '') + (', ' + escape(target.state) if target.state else '') + '</p>' if target.city else ''}
                </div>
                <table>
                    <thead>
                        <tr><th>Module</th><th>Source</th><th>Type</th><th>Title</th><th>Content</th><th>Confidence</th></tr>
                    </thead>
                    <tbody>{findings_rows}</tbody>
                </table>
            </div>"""

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{escape(inv.case_number)} — Investigation Report</title>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ font-family: -apple-system, sans-serif; background: #f8f9fa; color: #1a1a2e; padding: 2rem; line-height: 1.6; }}
    .report {{ max-width: 1100px; margin: 0 auto; background: #fff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); padding: 2.5rem; }}
    .header {{ border-bottom: 2px solid #3b82f6; padding-bottom: 1.5rem; margin-bottom: 2rem; }}
    .header h1 {{ font-size: 1.8rem; color: #1a1a2e; }}
    .header .meta {{ color: #6b7280; margin-top: 0.5rem; }}
    .summary {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin-bottom: 2rem; }}
    .summary-card {{ background: #f1f5f9; padding: 1rem; border-radius: 6px; text-align: center; }}
    .summary-card .number {{ font-size: 2rem; font-weight: 700; color: #3b82f6; }}
    .summary-card .label {{ color: #6b7280; font-size: 0.9rem; }}
    .target-section {{ margin-bottom: 2rem; padding: 1.5rem; background: #f8fafc; border-radius: 8px; border-left: 4px solid #3b82f6; }}
    .target-section h3 {{ margin-bottom: 0.75rem; }}
    .target-info p {{ color: #4b5563; font-size: 0.9rem; margin-bottom: 0.25rem; }}
    .badge {{ display: inline-block; padding: 0.15rem 0.5rem; background: #dbeafe; color: #1d4ed8; border-radius: 4px; font-size: 0.8rem; font-weight: 500; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; font-size: 0.85rem; }}
    th, td {{ padding: 0.5rem 0.75rem; text-align: left; border-bottom: 1px solid #e5e7eb; }}
    th {{ background: #f1f5f9; font-weight: 600; color: #374151; }}
    a {{ color: #3b82f6; }}
    .confidence-high {{ color: #16a34a; font-weight: 600; }}
    .confidence-medium {{ color: #d97706; font-weight: 600; }}
    .confidence-low {{ color: #dc2626; font-weight: 600; }}
    .footer {{ margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #e5e7eb; color: #9ca3af; font-size: 0.8rem; text-align: center; }}
    @media print {{ body {{ background: #fff; padding: 0; }} .report {{ box-shadow: none; }} }}
</style>
</head>
<body>
<div class="report">
    <div class="header">
        <h1>{escape(inv.case_number)} — {escape(inv.title)}</h1>
        <div class="meta">
            Status: {escape(inv.status)} | Created: {inv.created_at.strftime('%Y-%m-%d')}
            {' | ' + escape(inv.description) if inv.description else ''}
        </div>
    </div>

    <div class="summary">
        <div class="summary-card">
            <div class="number">{len(inv.targets)}</div>
            <div class="label">Targets</div>
        </div>
        <div class="summary-card">
            <div class="number">{total_findings}</div>
            <div class="label">Findings</div>
        </div>
        <div class="summary-card">
            <div class="number">{sum(len(t.module_runs) for t in inv.targets)}</div>
            <div class="label">Module Runs</div>
        </div>
    </div>

    {targets_html}

    <div class="footer">
        Generated by OSINT Investigation Suite v0.1.0 | {inv.created_at.strftime('%Y-%m-%d %H:%M')}
    </div>
</div>
</body>
</html>"""
