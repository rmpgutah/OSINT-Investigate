"""Government contracts module — USAspending API search."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class GovContractsModule(BaseModule):
    name = "gov_contracts"
    description = "Government contract search via USAspending API"

    USASPENDING_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"

    def applicable_target_types(self) -> list[str]:
        return ["organization"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        org_name = target.organization_name or target.label
        if not org_name:
            self.logger.info("No organization name, skipping gov contracts")
            return results

        # Query USAspending API
        contracts = await self._search_contracts(org_name)
        results.extend(contracts)

        # Summary
        results.append(
            ModuleResult(
                module_name=self.name,
                source="gov_contracts",
                finding_type="gov_contracts_summary",
                title=f"Government contracts for {org_name}",
                content=(
                    f"Found {len(contracts)} government contract record(s) "
                    f"for \"{org_name}\" via USAspending."
                ),
                data={
                    "organization": org_name,
                    "total_results": len(contracts),
                },
                confidence=75,
            )
        )

        return results

    # ------------------------------------------------------------------
    # USAspending API search
    # ------------------------------------------------------------------

    async def _search_contracts(self, org_name: str) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        payload = {
            "filters": {
                "keyword": org_name,
                "award_type_codes": ["A", "B", "C", "D"],
            },
            "fields": [
                "Award ID",
                "Recipient Name",
                "Description",
                "Award Amount",
                "Awarding Agency",
                "Start Date",
                "End Date",
            ],
            "page": 1,
            "limit": 10,
            "sort": "Award Amount",
            "order": "desc",
        }

        try:
            await self.limiter.acquire()
            response = await self.http.post(
                self.USASPENDING_URL,
                json=payload,
                timeout=15.0,
            )
            response.raise_for_status()
        except Exception as exc:
            self.logger.warning(f"USAspending API request failed: {exc}")
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse USAspending JSON response")
            return results

        awards = data.get("results", [])

        for award in awards[:10]:
            award_id = award.get("Award ID", "")
            recipient = award.get("Recipient Name", "")
            description = award.get("Description", "")
            amount = award.get("Award Amount", 0)
            agency = award.get("Awarding Agency", "")
            start_date = award.get("Start Date", "")
            end_date = award.get("End Date", "")

            amount_str = f"${amount:,.2f}" if isinstance(amount, (int, float)) else str(amount)

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="usaspending",
                    finding_type="gov_contract",
                    title=f"Contract {award_id}: {recipient} — {amount_str}",
                    content=(
                        f"Agency: {agency}. Amount: {amount_str}. "
                        f"Period: {start_date} to {end_date}. "
                        f"Description: {description[:150]}"
                    ),
                    data={
                        "award_id": award_id,
                        "recipient": recipient,
                        "description": description[:500],
                        "amount": amount,
                        "amount_formatted": amount_str,
                        "awarding_agency": agency,
                        "start_date": start_date,
                        "end_date": end_date,
                        "organization": org_name,
                    },
                    confidence=75,
                )
            )

        self.logger.info(
            f"USAspending returned {len(results)} contract(s) for '{org_name}'"
        )
        return results
