"""Port scan module -- queries Shodan InternetDB and Censys for open ports and services."""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    from duckduckgo_search import DDGS

    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False


class PortScanModule(BaseModule):
    name = "port_scan"
    description = "Open port, service, and vulnerability detection via Shodan InternetDB"

    INTERNETDB_URL = "https://internetdb.shodan.io/{ip}"

    def applicable_target_types(self) -> list[str]:
        return ["ip", "domain"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        ip = target.ip_address
        domain = target.domain or target.label

        # Resolve domain to IP if needed
        if not ip and domain:
            ip = await self._resolve_domain(domain)

        if not ip:
            self.logger.info("No IP available for target, skipping port scan")
            return results

        identifier = domain or ip

        # 1. Shodan InternetDB (free, no key)
        results.extend(await self._query_internetdb(ip, identifier))

        # 2. Censys dork search
        results.extend(await self._search_censys_dorks(identifier))

        # Summary
        ports = [r for r in results if r.finding_type == "open_port"]
        services = [r for r in results if r.finding_type == "service_detected"]
        vulns = [r for r in results if r.finding_type == "vulnerability"]

        results.append(
            ModuleResult(
                module_name=self.name,
                source="port_scan",
                finding_type="port_scan_summary",
                title=f"Port scan summary for {identifier}",
                content=(
                    f"Found {len(ports)} open port(s), "
                    f"{len(services)} service(s), "
                    f"{len(vulns)} vulnerability/vulnerabilities for \"{identifier}\"."
                ),
                data={
                    "ip": ip,
                    "domain": domain or "",
                    "open_ports": len(ports),
                    "services": len(services),
                    "vulnerabilities": len(vulns),
                    "total_findings": len(results),
                },
                confidence=70,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Shodan InternetDB
    # ------------------------------------------------------------------

    async def _query_internetdb(self, ip: str, identifier: str) -> list[ModuleResult]:
        """Query Shodan InternetDB for open ports, CPEs, vulns, and tags."""
        results: list[ModuleResult] = []
        url = self.INTERNETDB_URL.format(ip=ip)

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"InternetDB request failed: {exc}")
            return results

        if not response:
            self.logger.info("InternetDB returned no response")
            return results

        try:
            data: dict[str, Any] = response.json()
        except Exception:
            self.logger.warning("Failed to parse InternetDB JSON response")
            return results

        # Check for "no information" response
        if "detail" in data:
            self.logger.info(f"InternetDB: {data.get('detail', 'No info')}")
            return results

        ports = data.get("ports", [])
        hostnames = data.get("hostnames", [])
        cpes = data.get("cpes", [])
        vulns = data.get("vulns", [])
        tags = data.get("tags", [])

        # Open port findings
        for port in ports:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="shodan_internetdb",
                    finding_type="open_port",
                    title=f"Open port {port} on {identifier}",
                    content=f"Port {port} is open on {ip} ({identifier}).",
                    data={
                        "ip": ip,
                        "port": port,
                        "identifier": identifier,
                        "source": "shodan_internetdb",
                    },
                    confidence=85,
                )
            )

        # Service/CPE findings
        for cpe in cpes:
            # Parse CPE string: cpe:/a:vendor:product:version
            parts = cpe.split(":")
            vendor = parts[3] if len(parts) > 3 else "unknown"
            product = parts[4] if len(parts) > 4 else "unknown"
            version = parts[5] if len(parts) > 5 else ""

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="shodan_internetdb",
                    finding_type="service_detected",
                    title=f"Service: {vendor}/{product} {version} on {identifier}",
                    content=(
                        f"Detected service {product} by {vendor}"
                        + (f" version {version}" if version else "")
                        + f" on {ip}. CPE: {cpe}"
                    ),
                    data={
                        "ip": ip,
                        "cpe": cpe,
                        "vendor": vendor,
                        "product": product,
                        "version": version,
                        "identifier": identifier,
                        "source": "shodan_internetdb",
                    },
                    confidence=75,
                )
            )

        # Vulnerability findings
        for vuln in vulns:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="shodan_internetdb",
                    finding_type="vulnerability",
                    title=f"Vulnerability {vuln} on {identifier}",
                    content=f"Known vulnerability {vuln} detected on {ip} ({identifier}).",
                    data={
                        "ip": ip,
                        "cve": vuln,
                        "identifier": identifier,
                        "hostnames": hostnames,
                        "tags": tags,
                        "source": "shodan_internetdb",
                    },
                    confidence=90,
                )
            )

        self.logger.info(
            f"InternetDB returned {len(ports)} port(s), {len(cpes)} CPE(s), "
            f"{len(vulns)} vuln(s) for '{ip}'"
        )
        return results

    # ------------------------------------------------------------------
    # Censys DDG dork searches
    # ------------------------------------------------------------------

    async def _search_censys_dorks(self, identifier: str) -> list[ModuleResult]:
        """Search DuckDuckGo for Censys results about this target."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed -- skipping Censys dork searches"
            )
            return []

        queries = [
            f'site:search.censys.io "{identifier}"',
            f'"{identifier}" open port OR service OR banner',
        ]

        all_results: list[ModuleResult] = []
        seen_urls: set[str] = set()

        for query in queries:
            try:
                hits: list[dict[str, Any]] = await asyncio.to_thread(
                    self._sync_search, query
                )
            except Exception as exc:
                self.logger.warning(f"DDG dork search failed for '{query}': {exc}")
                continue

            for hit in hits[:5]:
                title = hit.get("title", "")
                url = hit.get("href", "")
                snippet = hit.get("body", "")

                if url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)

                finding_type = self._classify_finding(title, snippet)

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type=finding_type,
                        title=title or f"Port/service result for {identifier}",
                        content=snippet or None,
                        data={
                            "identifier": identifier,
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                        },
                        confidence=70 if finding_type == "service_detected" else 75,
                    )
                )

        self.logger.info(
            f"DDG dork searches found {len(all_results)} port-related results for '{identifier}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _resolve_domain(self, domain: str) -> str | None:
        """Resolve a domain name to an IP address."""
        try:
            infos = await asyncio.to_thread(
                socket.getaddrinfo, domain, None, socket.AF_INET
            )
            if infos:
                return infos[0][4][0]
        except (socket.gaierror, OSError) as exc:
            self.logger.warning(f"DNS resolution failed for {domain}: {exc}")
        return None

    @staticmethod
    def _classify_finding(title: str, snippet: str) -> str:
        """Classify finding type from search result content."""
        combined = f"{title} {snippet}".lower()
        if "vuln" in combined or "cve" in combined:
            return "vulnerability"
        if "port" in combined or "service" in combined or "banner" in combined:
            return "service_detected"
        return "open_port"

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
