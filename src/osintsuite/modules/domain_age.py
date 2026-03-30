"""Domain age module — RDAP creation date lookup and young-domain flagging."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class DomainAgeModule(BaseModule):
    name = "domain_age"
    description = "Domain age analysis via RDAP — flags young domains (<1 year)"

    RDAP_URL = "https://rdap.org/domain/{domain}"

    def applicable_target_types(self) -> list[str]:
        return ["domain"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        domain = target.domain or target.label
        if not domain:
            self.logger.info("No domain available on target, skipping domain age")
            return results

        # Strip protocol/path if present
        domain = domain.split("//")[-1].split("/")[0].strip().lower()

        # 1. RDAP lookup
        rdap_data = await self._fetch_rdap(domain)
        if rdap_data:
            results.extend(self._parse_rdap(domain, rdap_data))

        # Summary
        results.append(
            ModuleResult(
                module_name=self.name,
                source="domain_age",
                finding_type="domain_age_summary",
                title=f"Domain age analysis for {domain}",
                content=f"Analysed registration data for {domain}.",
                data={"domain": domain, "total_results": len(results)},
                confidence=80,
            )
        )
        return results

    # ------------------------------------------------------------------
    # RDAP fetch
    # ------------------------------------------------------------------

    async def _fetch_rdap(self, domain: str) -> dict[str, Any] | None:
        url = self.RDAP_URL.format(domain=domain)
        try:
            response = await self.fetch(url)
            if not response:
                return None
            return response.json()
        except Exception as exc:
            self.logger.warning(f"RDAP lookup failed for {domain}: {exc}")
            return None

    # ------------------------------------------------------------------
    # Parse RDAP response
    # ------------------------------------------------------------------

    def _parse_rdap(self, domain: str, data: dict[str, Any]) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        events = data.get("events", [])

        creation_date = None
        expiration_date = None
        updated_date = None

        for event in events:
            action = event.get("eventAction", "")
            date_str = event.get("eventDate", "")
            if action == "registration" and date_str:
                creation_date = date_str
            elif action == "expiration" and date_str:
                expiration_date = date_str
            elif action == "last changed" and date_str:
                updated_date = date_str

        if creation_date:
            try:
                created_dt = datetime.fromisoformat(
                    creation_date.replace("Z", "+00:00")
                )
                now = datetime.now(timezone.utc)
                age_days = (now - created_dt).days
                age_years = round(age_days / 365.25, 1)
                is_young = age_days < 365

                confidence = 80
                finding_data: dict[str, Any] = {
                    "domain": domain,
                    "creation_date": creation_date,
                    "age_days": age_days,
                    "age_years": age_years,
                    "is_young_domain": is_young,
                }
                if expiration_date:
                    finding_data["expiration_date"] = expiration_date
                if updated_date:
                    finding_data["last_updated"] = updated_date

                title = f"{domain} registered {age_years} years ago"
                if is_young:
                    title = f"YOUNG DOMAIN: {domain} is only {age_days} days old"
                    confidence = 90

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="rdap",
                        finding_type="domain_age",
                        title=title,
                        content=(
                            f"Domain {domain} was created on {creation_date[:10]}. "
                            f"Age: {age_years} years ({age_days} days)."
                        ),
                        data=finding_data,
                        confidence=confidence,
                    )
                )

                if is_young:
                    results.append(
                        ModuleResult(
                            module_name=self.name,
                            source="rdap",
                            finding_type="domain_age_flag",
                            title=f"Young domain alert: {domain}",
                            content=(
                                f"{domain} is less than 1 year old ({age_days} days). "
                                "Young domains are commonly used in phishing and fraud."
                            ),
                            data=finding_data,
                            confidence=85,
                        )
                    )
            except Exception as exc:
                self.logger.warning(f"Failed to parse RDAP date: {exc}")

        # Registrar info
        entities = data.get("entities", [])
        for entity in entities:
            roles = entity.get("roles", [])
            if "registrar" in roles:
                vcard = entity.get("vcardArray", [None, []])
                registrar_name = ""
                if len(vcard) > 1:
                    for field in vcard[1]:
                        if field[0] == "fn":
                            registrar_name = field[3] if len(field) > 3 else ""
                            break
                if registrar_name:
                    results.append(
                        ModuleResult(
                            module_name=self.name,
                            source="rdap",
                            finding_type="domain_registrar",
                            title=f"Registrar for {domain}: {registrar_name}",
                            content=f"Domain {domain} is registered through {registrar_name}.",
                            data={
                                "domain": domain,
                                "registrar": registrar_name,
                            },
                            confidence=80,
                        )
                    )
                break

        return results
