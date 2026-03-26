"""Email intelligence module — validates emails, checks MX records, HIBP breaches."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import dns.resolver

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class EmailIntelModule(BaseModule):
    name = "email_intel"
    description = "Email validation, MX records, and breach checking"

    def __init__(self, *args, hibp_api_key: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.hibp_api_key = hibp_api_key

    def applicable_target_types(self) -> list[str]:
        return ["email", "person"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        email = target.email or (target.label if target.target_type == "email" else None)
        if not email:
            return results

        # Validate format
        results.append(self._validate_format(email))

        # Check MX records
        mx_result = await self._check_mx(email)
        if mx_result:
            results.append(mx_result)

        # HIBP breach check (if API key available)
        if self.hibp_api_key:
            breach_results = await self._check_hibp(email)
            results.extend(breach_results)

        return results

    def _validate_format(self, email: str) -> ModuleResult:
        """Validate email format."""
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        is_valid = bool(re.match(pattern, email))
        return ModuleResult(
            module_name=self.name,
            source="format_validation",
            finding_type="validation",
            title=f"Email format: {'valid' if is_valid else 'invalid'}",
            content=email,
            data={"email": email, "format_valid": is_valid},
            confidence=95 if is_valid else 95,
        )

    async def _check_mx(self, email: str) -> ModuleResult | None:
        """Check MX records for the email domain."""
        domain = email.split("@")[1]
        try:
            resolver = dns.resolver.Resolver()
            resolver.timeout = 5
            resolver.lifetime = 5
            answers = resolver.resolve(domain, "MX")
            mx_records = [
                {"priority": r.preference, "host": str(r.exchange).rstrip(".")}
                for r in answers
            ]
            return ModuleResult(
                module_name=self.name,
                source="dns_mx",
                finding_type="mx_records",
                title=f"MX records for {domain}",
                content="\n".join(f"{r['priority']} {r['host']}" for r in mx_records),
                data={"domain": domain, "mx_records": mx_records, "has_mx": True},
                confidence=90,
            )
        except (dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
            return ModuleResult(
                module_name=self.name,
                source="dns_mx",
                finding_type="mx_records",
                title=f"No MX records for {domain}",
                content=f"Domain {domain} has no MX records",
                data={"domain": domain, "has_mx": False},
                confidence=90,
            )
        except Exception as e:
            self.logger.warning(f"MX lookup failed for {domain}: {e}")
            return None

    async def _check_hibp(self, email: str) -> list[ModuleResult]:
        """Check Have I Been Pwned for breaches (requires API key)."""
        url = f"https://haveibeenpwned.com/api/v3/breachedaccount/{email}"
        headers = {
            "hibp-api-key": self.hibp_api_key,
            "User-Agent": "OSINT-Suite",
        }
        response = await self.fetch(url, headers=headers)
        if not response:
            return []

        if response.status_code == 404:
            return [
                ModuleResult(
                    module_name=self.name,
                    source="hibp",
                    finding_type="breach_check",
                    title=f"No breaches found for {email}",
                    content="Email not found in any known breaches",
                    data={"email": email, "breached": False, "breach_count": 0},
                    confidence=85,
                )
            ]

        breaches = response.json()
        results = []
        for breach in breaches:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="hibp",
                    finding_type="breach",
                    title=f"Breach: {breach.get('Name', 'Unknown')}",
                    content=breach.get("Description", ""),
                    data={
                        "email": email,
                        "breach_name": breach.get("Name"),
                        "breach_date": breach.get("BreachDate"),
                        "data_classes": breach.get("DataClasses", []),
                    },
                    confidence=90,
                )
            )
        return results
