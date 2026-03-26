"""Cross-reference findings across targets within an investigation.

Identifies connections between targets by matching shared data points
(emails, phones, addresses, URLs) across their findings.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import UUID

from thefuzz import fuzz

from osintsuite.db.repository import Repository

logger = logging.getLogger(__name__)


@dataclass
class Correlation:
    """A connection found between two targets."""

    target_a_id: UUID
    target_a_label: str
    target_b_id: UUID
    target_b_label: str
    field: str
    value_a: str
    value_b: str
    match_type: str  # "exact" or "fuzzy"
    similarity: int  # 0-100


class Correlator:
    """Cross-references findings to discover connections between targets."""

    MATCH_FIELDS = ["email", "phone", "address", "url", "username"]
    FUZZY_THRESHOLD = 80

    def __init__(self, db: Repository):
        self.db = db

    async def correlate_investigation(
        self, investigation_id: UUID
    ) -> list[Correlation]:
        """Find correlations between all targets in an investigation."""
        targets = await self.db.list_targets(investigation_id)
        if len(targets) < 2:
            return []

        # Collect data points per target
        target_data: dict[UUID, dict[str, set[str]]] = {}
        for target in targets:
            findings = await self.db.get_findings_by_target(target.id)
            data_points: dict[str, set[str]] = {f: set() for f in self.MATCH_FIELDS}

            # From target fields
            if target.email:
                data_points["email"].add(target.email.lower())
            if target.phone:
                data_points["phone"].add(target.phone)
            if target.address:
                data_points["address"].add(target.address.lower())

            # From findings
            for finding in findings:
                for field in self.MATCH_FIELDS:
                    if field in finding.data:
                        val = finding.data[field]
                        if isinstance(val, str) and val:
                            data_points[field].add(val.lower())
                        elif isinstance(val, list):
                            for v in val:
                                if isinstance(v, str) and v:
                                    data_points[field].add(v.lower())

            target_data[target.id] = data_points

        # Compare all pairs
        correlations: list[Correlation] = []
        target_list = list(targets)
        for i in range(len(target_list)):
            for j in range(i + 1, len(target_list)):
                t_a = target_list[i]
                t_b = target_list[j]
                data_a = target_data[t_a.id]
                data_b = target_data[t_b.id]

                for field in self.MATCH_FIELDS:
                    for val_a in data_a[field]:
                        for val_b in data_b[field]:
                            if val_a == val_b:
                                correlations.append(
                                    Correlation(
                                        target_a_id=t_a.id,
                                        target_a_label=t_a.label,
                                        target_b_id=t_b.id,
                                        target_b_label=t_b.label,
                                        field=field,
                                        value_a=val_a,
                                        value_b=val_b,
                                        match_type="exact",
                                        similarity=100,
                                    )
                                )
                            elif field in ("address", "email"):
                                score = fuzz.ratio(val_a, val_b)
                                if score >= self.FUZZY_THRESHOLD:
                                    correlations.append(
                                        Correlation(
                                            target_a_id=t_a.id,
                                            target_a_label=t_a.label,
                                            target_b_id=t_b.id,
                                            target_b_label=t_b.label,
                                            field=field,
                                            value_a=val_a,
                                            value_b=val_b,
                                            match_type="fuzzy",
                                            similarity=score,
                                        )
                                    )

        logger.info(
            f"Found {len(correlations)} correlations across "
            f"{len(targets)} targets"
        )
        return correlations
