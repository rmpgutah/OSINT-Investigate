"""Domain reconnaissance module — WHOIS, DNS records, and subdomain discovery."""

from __future__ import annotations

from typing import TYPE_CHECKING

import dns.resolver
import whois

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class DomainReconModule(BaseModule):
    name = "domain_recon"
    description = "WHOIS lookup, DNS records, and domain intelligence"

    def applicable_target_types(self) -> list[str]:
        return ["domain"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        domain = target.label

        # Strip protocol if present
        if "://" in domain:
            domain = domain.split("://")[1].split("/")[0]

        results.extend(await self._whois_lookup(domain))
        results.extend(await self._dns_records(domain))

        return results

    async def _whois_lookup(self, domain: str) -> list[ModuleResult]:
        """Perform WHOIS lookup on domain."""
        try:
            w = whois.whois(domain)
            data = {}
            for key in [
                "domain_name", "registrar", "creation_date", "expiration_date",
                "name_servers", "status", "emails", "org", "address",
                "city", "state", "country",
            ]:
                val = getattr(w, key, None)
                if val is not None:
                    if isinstance(val, list):
                        data[key] = [str(v) for v in val]
                    else:
                        data[key] = str(val)

            content_lines = [f"{k}: {v}" for k, v in data.items()]
            return [
                ModuleResult(
                    module_name=self.name,
                    source="whois",
                    finding_type="whois_record",
                    title=f"WHOIS for {domain}",
                    content="\n".join(content_lines),
                    data=data,
                    confidence=85,
                    raw_response=str(w),
                )
            ]
        except Exception as e:
            self.logger.warning(f"WHOIS lookup failed for {domain}: {e}")
            return [
                ModuleResult(
                    module_name=self.name,
                    source="whois",
                    finding_type="error",
                    title=f"WHOIS failed for {domain}",
                    content=str(e),
                    data={"domain": domain, "error": str(e)},
                    confidence=0,
                )
            ]

    async def _dns_records(self, domain: str) -> list[ModuleResult]:
        """Query DNS records for A, AAAA, MX, TXT, NS, CNAME."""
        results = []
        resolver = dns.resolver.Resolver()
        resolver.timeout = 5
        resolver.lifetime = 5

        record_types = ["A", "AAAA", "MX", "TXT", "NS", "CNAME", "SOA"]
        all_records: dict[str, list[str]] = {}

        for rtype in record_types:
            try:
                answers = resolver.resolve(domain, rtype)
                records = [str(r).rstrip(".") for r in answers]
                all_records[rtype] = records
            except (
                dns.resolver.NoAnswer,
                dns.resolver.NXDOMAIN,
                dns.resolver.NoNameservers,
                dns.resolver.Timeout,
            ):
                continue
            except Exception as e:
                self.logger.debug(f"DNS {rtype} query failed for {domain}: {e}")
                continue

        if all_records:
            content_lines = []
            for rtype, records in all_records.items():
                for record in records:
                    content_lines.append(f"{rtype}: {record}")

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="dns",
                    finding_type="dns_records",
                    title=f"DNS records for {domain}",
                    content="\n".join(content_lines),
                    data={"domain": domain, "records": all_records},
                    confidence=90,
                )
            )

        return results
