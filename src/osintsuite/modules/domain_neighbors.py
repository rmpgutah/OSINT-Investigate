"""Domain neighbors / reverse IP lookup module."""

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


class DomainNeighborsModule(BaseModule):
    name = "domain_neighbors"
    description = "Reverse IP lookup and shared hosting / domain neighbor discovery"

    HACKERTARGET_REVERSE_IP = "https://api.hackertarget.com/reverseiplookup/"
    MAX_RESULTS = 20

    def applicable_target_types(self) -> list[str]:
        return ["domain", "ip"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        label = target.label or ""
        if not label:
            self.logger.info("No label available on target, skipping domain neighbors")
            return results

        # Determine IP address
        ip_address = await self._resolve_ip(label)

        if ip_address:
            # 1. HackerTarget reverse IP lookup
            results.extend(await self._reverse_ip_lookup(ip_address, label))

        # 2. DDG dork searches
        search_term = ip_address or label
        results.extend(await self._search_dorks(search_term))

        # Deduplicate by domain/URL
        seen: set[str] = set()
        deduped: list[ModuleResult] = []
        for r in results:
            key = r.data.get("domain", r.data.get("url", r.title))
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            deduped.append(r)

        trimmed = deduped[: self.MAX_RESULTS]

        # Summary finding
        trimmed.append(
            ModuleResult(
                module_name=self.name,
                source="domain_neighbors",
                finding_type="domain_neighbors_summary",
                title=f"Domain neighbors summary for {label}",
                content=(
                    f"Found {len(trimmed)} neighbor domain(s) for "
                    f'"{label}" (IP: {ip_address or "unresolved"}) '
                    f"via reverse IP lookup and DuckDuckGo searches."
                ),
                data={
                    "target": label,
                    "ip_address": ip_address or "",
                    "total_results": len(trimmed),
                },
                confidence=65,
            )
        )

        return trimmed

    # ------------------------------------------------------------------
    # Resolve domain to IP
    # ------------------------------------------------------------------

    async def _resolve_ip(self, label: str) -> str:
        """Resolve a domain to its IP address, or return as-is if already an IP."""
        # Simple check: if it looks like an IP, use it directly
        parts = label.split(".")
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            return label

        try:
            ip = await asyncio.to_thread(socket.gethostbyname, label)
            self.logger.info(f"Resolved {label} to {ip}")
            return ip
        except socket.gaierror as exc:
            self.logger.warning(f"Failed to resolve {label}: {exc}")
            return ""

    # ------------------------------------------------------------------
    # HackerTarget reverse IP lookup
    # ------------------------------------------------------------------

    async def _reverse_ip_lookup(
        self, ip: str, original_label: str
    ) -> list[ModuleResult]:
        """Query HackerTarget free reverse IP API."""
        results: list[ModuleResult] = []

        url = f"{self.HACKERTARGET_REVERSE_IP}?q={ip}"

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"HackerTarget reverse IP request failed: {exc}")
            return results

        if not response:
            self.logger.info("HackerTarget returned no response")
            return results

        try:
            body = response.text
        except Exception:
            self.logger.warning("Failed to read HackerTarget response body")
            return results

        if not body or "error" in body.lower() or "API count exceeded" in body:
            self.logger.warning(f"HackerTarget error or rate limit: {body[:200]}")
            return results

        domains = [
            line.strip()
            for line in body.splitlines()
            if line.strip() and line.strip() != original_label
        ]

        for domain in domains[:15]:
            finding_type = "shared_host" if domain != original_label else "neighbor_domain"

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="hackertarget",
                    finding_type=finding_type,
                    title=f"Shared host: {domain}",
                    content=f"Domain {domain} shares IP {ip} with {original_label}",
                    data={
                        "domain": domain,
                        "ip": ip,
                        "source": "hackertarget_reverse_ip",
                        "url": f"http://{domain}",
                    },
                    confidence=75,
                )
            )

        self.logger.info(
            f"HackerTarget reverse IP found {len(results)} neighbor(s) for {ip}"
        )
        return results

    # ------------------------------------------------------------------
    # DuckDuckGo dork searches
    # ------------------------------------------------------------------

    async def _search_dorks(self, search_term: str) -> list[ModuleResult]:
        """Run DuckDuckGo dork queries for domain neighbors."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping dork searches"
            )
            return []

        queries = [
            f'"{search_term}" hosted sites OR shared hosting',
            f'"{search_term}" reverse IP',
        ]

        all_results: list[ModuleResult] = []

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

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="neighbor_domain",
                        title=title or f"Domain neighbor result for {search_term}",
                        content=snippet or None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                        },
                        confidence=70,
                    )
                )

        self.logger.info(
            f"DDG neighbor searches found {len(all_results)} results for '{search_term}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
