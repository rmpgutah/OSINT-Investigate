"""Shodan host intelligence module — open ports, services, banners, vulnerabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class ShodanIntelModule(BaseModule):
    name = "shodan_intel"
    description = "Shodan host intelligence: open ports, services, banners, vulnerabilities"

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        rate_limiter,
        shodan_api_key: str | None = None,
    ):
        super().__init__(http_client, rate_limiter)
        self.shodan_api_key = shodan_api_key

    def applicable_target_types(self) -> list[str]:
        return ["ip", "domain"]

    async def run(self, target: Target) -> list[ModuleResult]:
        if not self.shodan_api_key:
            return []

        results: list[ModuleResult] = []
        indicator = target.label

        # For domain targets, resolve to IP first
        if target.target_type == "domain":
            ip = await self._resolve_domain(indicator)
            if not ip:
                return []
        else:
            ip = indicator

        host_data = await self._host_lookup(ip)
        if not host_data:
            return []

        # --- Host summary ---
        org = host_data.get("org", "Unknown")
        results.append(
            ModuleResult(
                module_name=self.name,
                source="shodan",
                finding_type="shodan_host_summary",
                title=f"Shodan: {ip} ({org})",
                content=None,
                data={
                    "ip": ip,
                    "org": org,
                    "isp": host_data.get("isp"),
                    "os": host_data.get("os"),
                    "ports": host_data.get("ports", []),
                    "hostnames": host_data.get("hostnames", []),
                    "last_update": host_data.get("last_update"),
                    "country_code": host_data.get("country_code"),
                    "city": host_data.get("city"),
                },
                confidence=80,
            )
        )

        # --- Per-service findings (max 20) ---
        for service in host_data.get("data", [])[:20]:
            port = service.get("port", 0)
            transport = service.get("transport", "tcp")
            product = service.get("product", "")
            version = service.get("version", "")
            banner = service.get("data", "")

            title_parts = [f"Port {port}/{transport}:"]
            if product:
                title_parts.append(product)
            if version:
                title_parts.append(version)

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="shodan",
                    finding_type="shodan_service",
                    title=" ".join(title_parts),
                    content=banner[:500] if banner else None,
                    data={
                        "ip": ip,
                        "port": port,
                        "transport": transport,
                        "product": product,
                        "version": version,
                        "cpe": service.get("cpe", []),
                        "banner": banner,
                    },
                    confidence=75,
                )
            )

        # --- Vulnerability findings ---
        vulns = host_data.get("vulns", [])
        for cve_id in vulns:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="shodan",
                    finding_type="shodan_vulnerability",
                    title=f"CVE: {cve_id}",
                    content=None,
                    data={"ip": ip, "cve_id": cve_id},
                    confidence=85,
                )
            )

        return results

    async def _resolve_domain(self, domain: str) -> str | None:
        """Resolve a domain to an IP address via Shodan DNS API."""
        try:
            resp = await self.fetch(
                "https://api.shodan.io/dns/resolve",
                params={"hostnames": domain, "key": self.shodan_api_key},
            )
            if resp is None:
                return None

            data = resp.json()
            return data.get(domain)
        except Exception as e:
            self.logger.debug(f"Shodan DNS resolve failed for {domain}: {e}")
            return None

    async def _host_lookup(self, ip: str) -> dict | None:
        """Fetch Shodan host information for an IP address."""
        try:
            resp = await self.fetch(
                f"https://api.shodan.io/shodan/host/{ip}",
                params={"key": self.shodan_api_key},
            )
            if resp is None:
                return None

            return resp.json()
        except Exception as e:
            self.logger.debug(f"Shodan host lookup failed for {ip}: {e}")
            return None
