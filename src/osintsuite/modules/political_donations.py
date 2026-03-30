"""Federal political contribution records via FEC API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class PoliticalDonationsModule(BaseModule):
    name = "political_donations"
    description = "Federal political contribution records via FEC API"

    FEC_API_BASE = "https://api.open.fec.gov/v1/schedules/schedule_a/"
    FEC_API_KEY = "DEMO_KEY"

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        full_name = target.full_name or target.label
        results: list[ModuleResult] = []

        contributions = await self._fetch_contributions(full_name)

        # Individual donation findings (max 20)
        total_amount = 0.0
        dates: list[str] = []
        recipients: dict[str, float] = {}

        for contrib in contributions[:20]:
            contributor_name = contrib.get("contributor_name", "")
            committee = contrib.get("committee", {}).get("name", "") if isinstance(
                contrib.get("committee"), dict
            ) else contrib.get("committee_name", "")
            amount = contrib.get("contribution_receipt_amount", 0) or 0
            date = contrib.get("contribution_receipt_date", "")
            city = contrib.get("contributor_city", "")
            state = contrib.get("contributor_state", "")

            total_amount += float(amount)
            if date:
                dates.append(date)
            if committee:
                recipients[committee] = recipients.get(committee, 0) + float(amount)

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="fec_api",
                    finding_type="political_donation",
                    title=f"${amount:,.2f} to {committee}" if committee else f"${amount:,.2f} contribution",
                    content=f"{contributor_name} — {city}, {state}" if city else contributor_name,
                    data={
                        "contributor_name": contributor_name,
                        "committee": committee,
                        "amount": float(amount),
                        "date": date,
                        "city": city,
                        "state": state,
                    },
                    confidence=80,
                )
            )

        # Summary finding
        sorted_dates = sorted(d for d in dates if d)
        date_range = ""
        if sorted_dates:
            date_range = f"{sorted_dates[0]} to {sorted_dates[-1]}"

        top_recipients = sorted(
            recipients.items(), key=lambda x: x[1], reverse=True
        )[:5]

        results.append(
            ModuleResult(
                module_name=self.name,
                source="fec_api",
                finding_type="donations_summary",
                title=f"Donations summary for {full_name} ({len(contributions[:20])} records)",
                content=None,
                data={
                    "full_name": full_name,
                    "total_amount": round(total_amount, 2),
                    "total_donations": len(contributions[:20]),
                    "date_range": date_range,
                    "top_recipients": [
                        {"committee": name, "total": round(total, 2)}
                        for name, total in top_recipients
                    ],
                },
                confidence=75,
            )
        )

        return results

    # ------------------------------------------------------------------
    # FEC API call
    # ------------------------------------------------------------------

    async def _fetch_contributions(self, name: str) -> list[dict[str, Any]]:
        encoded_name = quote_plus(name)
        url = (
            f"{self.FEC_API_BASE}"
            f"?contributor_name={encoded_name}"
            f"&sort=-contribution_receipt_date"
            f"&per_page=20"
            f"&api_key={self.FEC_API_KEY}"
        )
        try:
            resp = await self.fetch(url)
            if resp is None:
                return []
            data = resp.json()
            return data.get("results", [])
        except Exception as exc:
            self.logger.warning(f"FEC API request failed: {exc}")
            return []
