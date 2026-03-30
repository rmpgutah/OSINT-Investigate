"""FCC radio frequency license lookups."""

from __future__ import annotations

from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class RadioFreqModule(BaseModule):
    name = "radio_freq"
    description = "FCC radio frequency license lookups"

    def applicable_target_types(self) -> list[str]:
        return ["person", "organization"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        name = target.label

        licenses = await self._fcc_search(name)

        if licenses is None:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="fcc_uls",
                    finding_type="fcc_license",
                    title=f"FCC ULS API unavailable for {name}",
                    content="Could not retrieve license data from FCC ULS.",
                    data={"error": "api_unavailable", "search_name": name},
                    confidence=10,
                )
            )
            return results

        count = 0
        for lic in licenses[:15]:
            count += 1
            lic_name = lic.get("licName", "")
            frn = lic.get("frn", "")
            callsign = lic.get("callsign", "")
            service = lic.get("serviceDesc", "")
            status = lic.get("statusDesc", "")
            effective = lic.get("effectiveDate", "")
            expiration = lic.get("expiredDate", "")

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="fcc_uls",
                    finding_type="fcc_license",
                    title=f"FCC License: {callsign} — {lic_name}",
                    content=(
                        f"Licensee: {lic_name}, Callsign: {callsign}, "
                        f"Service: {service}, Status: {status}, "
                        f"Effective: {effective}, Expires: {expiration}"
                    ),
                    data={
                        "licensee_name": lic_name,
                        "callsign": callsign,
                        "service": service,
                        "status": status,
                        "effective_date": effective,
                        "expiration_date": expiration,
                        "frn": frn,
                    },
                    confidence=85,
                )
            )

        # Summary
        results.append(
            ModuleResult(
                module_name=self.name,
                source="fcc_uls",
                finding_type="fcc_summary",
                title=f"FCC license summary for {name}: {count} licenses found",
                content=f"Searched FCC ULS for '{name}'. Found {count} license(s).",
                data={
                    "search_name": name,
                    "total_licenses": count,
                },
                confidence=80,
            )
        )

        return results

    async def _fcc_search(self, name: str) -> list[dict] | None:
        """Query the FCC ULS basic search API."""
        url = "https://data.fcc.gov/api/license-view/basicSearch/getLicenses"
        params = {
            "searchValue": name,
            "format": "json",
        }

        try:
            resp = await self.fetch(url, params=params)
            if resp is None:
                return None

            data = resp.json()

            # FCC API wraps results in Licenses -> License
            licenses_wrapper = data.get("Licenses", {})
            if not licenses_wrapper:
                return []

            license_list = licenses_wrapper.get("License", [])
            # If only one result, FCC returns a dict instead of a list
            if isinstance(license_list, dict):
                license_list = [license_list]

            return license_list
        except Exception as e:
            self.logger.warning(f"FCC ULS search failed for '{name}': {e}")
            return None
