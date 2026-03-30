"""Subdomain enumeration module — certificate transparency and brute-force discovery."""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


COMMON_PREFIXES = [
    "www", "mail", "ftp", "admin", "dev", "staging", "api", "app", "cdn",
    "test", "beta", "blog", "shop", "portal", "vpn", "remote", "gateway",
    "m", "mobile", "ns1", "ns2",
]


class SubdomainEnumModule(BaseModule):
    name = "subdomain_enum"
    description = "Subdomain discovery via certificate transparency and brute-force"

    def applicable_target_types(self) -> list[str]:
        return ["domain"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        domain = target.label

        # Strip protocol if present
        if "://" in domain:
            domain = domain.split("://")[1].split("/")[0]

        # Collect unique subdomains from all sources
        discovered: dict[str, dict] = {}  # subdomain -> {ip, source}

        # Source 1: crt.sh certificate transparency
        crt_subs = await self._crtsh_search(domain)
        for sub, ip in crt_subs.items():
            if sub not in discovered:
                discovered[sub] = {"ip": ip, "source": "crt.sh"}

        # Source 2: HackerTarget host search
        ht_subs = await self._hackertarget_search(domain)
        for sub, ip in ht_subs.items():
            if sub not in discovered:
                discovered[sub] = {"ip": ip, "source": "hackertarget"}
            elif not discovered[sub]["ip"] and ip:
                discovered[sub]["ip"] = ip

        # Source 3: Brute-force common prefixes
        bf_subs = await self._bruteforce_subdomains(domain)
        for sub, ip in bf_subs.items():
            if sub not in discovered:
                discovered[sub] = {"ip": ip, "source": "bruteforce"}

        # Produce a result per subdomain
        for subdomain, info in sorted(discovered.items()):
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source=info["source"],
                    finding_type="subdomain",
                    title=f"Subdomain: {subdomain}",
                    content=f"{subdomain} -> {info['ip'] or 'unresolved'}",
                    data={
                        "subdomain": subdomain,
                        "ip": info["ip"],
                        "source": info["source"],
                        "parent_domain": domain,
                    },
                    confidence=80 if info["ip"] else 50,
                )
            )

        # Summary
        resolved_count = sum(1 for v in discovered.values() if v["ip"])
        results.append(
            ModuleResult(
                module_name=self.name,
                source="subdomain_enum",
                finding_type="subdomain_summary",
                title=f"Subdomain enumeration summary for {domain}",
                content=(
                    f"Discovered {len(discovered)} unique subdomain(s), "
                    f"{resolved_count} resolving to an IP address."
                ),
                data={
                    "domain": domain,
                    "total_subdomains": len(discovered),
                    "resolved_count": resolved_count,
                    "sources": ["crt.sh", "hackertarget", "bruteforce"],
                },
                confidence=75,
            )
        )

        return results

    async def _crtsh_search(self, domain: str) -> dict[str, str]:
        """Query crt.sh certificate transparency logs for subdomains."""
        subdomains: dict[str, str] = {}
        url = f"https://crt.sh/?q=%.{domain}&output=json"

        response = await self.fetch(url, timeout=30.0)
        if not response:
            return subdomains

        try:
            entries = response.json()
            for entry in entries:
                name_value = entry.get("name_value", "")
                for name in name_value.split("\n"):
                    name = name.strip().lower()
                    # Remove wildcard prefix
                    if name.startswith("*."):
                        name = name[2:]
                    if name.endswith(f".{domain}") or name == domain:
                        if name not in subdomains:
                            subdomains[name] = ""
        except Exception as e:
            self.logger.warning(f"crt.sh parsing failed for {domain}: {e}")

        return subdomains

    async def _hackertarget_search(self, domain: str) -> dict[str, str]:
        """Query HackerTarget free API for subdomains."""
        subdomains: dict[str, str] = {}
        url = f"https://api.hackertarget.com/hostsearch/?q={domain}"

        response = await self.fetch(url)
        if not response:
            return subdomains

        text = response.text.strip()
        if not text or "error" in text.lower() or "API count exceeded" in text:
            return subdomains

        for line in text.splitlines():
            parts = line.strip().split(",")
            if len(parts) >= 2:
                hostname = parts[0].strip().lower()
                ip = parts[1].strip()
                subdomains[hostname] = ip

        return subdomains

    async def _bruteforce_subdomains(self, domain: str) -> dict[str, str]:
        """Check common subdomain prefixes via DNS resolution."""
        subdomains: dict[str, str] = {}

        async def _resolve(prefix: str) -> tuple[str, str]:
            fqdn = f"{prefix}.{domain}"
            try:
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    None, lambda: socket.getaddrinfo(fqdn, None, socket.AF_INET)
                )
                if result:
                    ip = result[0][4][0]
                    return fqdn, ip
            except (socket.gaierror, OSError):
                pass
            return fqdn, ""

        # Resolve in batches to avoid overwhelming DNS
        tasks = [_resolve(prefix) for prefix in COMMON_PREFIXES]
        resolved = await asyncio.gather(*tasks, return_exceptions=True)

        for item in resolved:
            if isinstance(item, Exception):
                continue
            fqdn, ip = item
            if ip:
                subdomains[fqdn] = ip

        return subdomains
