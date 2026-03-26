"""JSON report format — structured data export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from osintsuite.db.models import Investigation


class JSONReport:
    def render(self, investigation: Investigation, output_path: Path) -> None:
        data = {
            "case_number": investigation.case_number,
            "title": investigation.title,
            "description": investigation.description,
            "status": investigation.status,
            "created_at": investigation.created_at.isoformat(),
            "targets": [
                {
                    "id": str(target.id),
                    "type": target.target_type,
                    "label": target.label,
                    "full_name": target.full_name,
                    "email": target.email,
                    "phone": target.phone,
                    "address": target.address,
                    "date_of_birth": str(target.date_of_birth) if target.date_of_birth else None,
                    "city": target.city,
                    "state": target.state,
                    "findings": [
                        {
                            "id": str(f.id),
                            "module": f.module_name,
                            "source": f.source,
                            "type": f.finding_type,
                            "title": f.title,
                            "content": f.content,
                            "data": f.data,
                            "confidence": f.confidence,
                            "created_at": f.created_at.isoformat(),
                        }
                        for f in target.findings
                    ],
                    "module_runs": [
                        {
                            "module": r.module_name,
                            "status": r.status,
                            "findings_count": r.findings_count,
                            "started_at": r.started_at.isoformat() if r.started_at else None,
                            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                            "error": r.error_message,
                        }
                        for r in target.module_runs
                    ],
                }
                for target in investigation.targets
            ],
        }

        output_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
