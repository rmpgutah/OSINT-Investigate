"""DNS MX/SPF/DMARC module — checks email security DNS records for a domain."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    from duckduckgo_search import DDGS

    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False


class DnsMxSpfModule(BaseModule):
    name = "dns_mx_spf"
    description = "DNS MX, SPF, and DMARC record lookup for email security assessment"

    MAX_RESULTS = 15

    def applicable_target_types(self) -> list[str]:
        return ["domain"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        domain = target.label
        if not domain:
            self.logger.info("No domain available on target, skipping DNS MX/SPF")
            return results

        # 1. MX record lookup via DDG
        results.extend(await self._search_mx(domain))

        # 2. SPF record lookup via DDG
        results.extend(await self._search_spf(domain))

        # 3. DMARC record lookup via DDG
        results.extend(await self._search_dmarc(domain))

        # Summary finding
        results.append(
            ModuleResult(
                module_name=self.name,
                source="dns_mx_spf",
                finding_type="email_security_summary",
                title=f"Email security summary for {domain}",
                content=(
                    f"Found {len(results)} email security DNS record(s) for "
                    f'"{domain}" across MX, SPF, and DMARC lookups.'
                ),
                data={
                    "domain": domain,
                    "total_results": len(results),
                },
                confidence=75,
            )
        )

        return results

    # ------------------------------------------------------------------
    # MX lookup
    # ------------------------------------------------------------------

    async def _search_mx(self, domain: str) -> list[ModuleResult]:
        """Search for MX records via mxtoolbox and DDG."""
        results: list[ModuleResult] = []

        # Try mxtoolbox API endpoint
        url = f"https://mxtoolbox.com/api/v1/lookup/mx/{domain}"
        try:
            response = await self.fetch(url)
            if response and response.status_code == 200:
                try:
                    data = response.json()
                    records = data if isinstance(data, list) else data.get("records", [])
                    for rec in records[:5]:
                        mx_host = rec.get("hostname", rec.get("value", ""))
                        priority = rec.get("priority", rec.get("preference", ""))
                        results.append(
                            ModuleResult(
                                module_name=self.name,
                                source="mxtoolbox",
                                finding_type="mx_record",
                                title=f"MX record: {mx_host} (priority {priority})",
                                content=f"MX record for {domain}: {mx_host} with priority {priority}",
                                data={
                                    "domain": domain,
                                    "mx_host": mx_host,
                                    "priority": priority,
                                    "source": "mxtoolbox",
                                },
                                confidence=80,
                            )
                        )
                except Exception:
                    pass
        except Exception as exc:
            self.logger.warning(f"MXToolbox API request failed: {exc}")

        # DDG fallback
        if not results and _HAS_DDGS:
            query = f"mxtoolbox.com {domain} MX records"
            try:
                hits: list[dict[str, Any]] = await asyncio.to_thread(
                    self._sync_search, query
                )
                for hit in hits[:3]:
                    results.append(
                        ModuleResult(
                            module_name=self.name,
                            source="duckduckgo",
                            finding_type="mx_record",
                            title=hit.get("title", f"MX record result for {domain}"),
                            content=hit.get("body", None),
                            data={
                                "domain": domain,
                                "url": hit.get("href", ""),
                                "snippet": hit.get("body", ""),
                                "source": "duckduckgo_dork",
                            },
                            confidence=80,
                        )
                    )
            except Exception as exc:
                self.logger.warning(f"DDG MX search failed: {exc}")

        return results

    # ------------------------------------------------------------------
    # SPF lookup
    # ------------------------------------------------------------------

    async def _search_spf(self, domain: str) -> list[ModuleResult]:
        """Search for SPF records via DDG."""
        if not _HAS_DDGS:
            return []

        results: list[ModuleResult] = []
        query = f'"{domain}" SPF record'
        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
            for hit in hits[:3]:
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="spf_record",
                        title=hit.get("title", f"SPF record for {domain}"),
                        content=hit.get("body", None),
                        data={
                            "domain": domain,
                            "url": hit.get("href", ""),
                            "snippet": hit.get("body", ""),
                            "source": "duckduckgo_dork",
                        },
                        confidence=80,
                    )
                )
        except Exception as exc:
            self.logger.warning(f"DDG SPF search failed: {exc}")

        return results

    # ------------------------------------------------------------------
    # DMARC lookup
    # ------------------------------------------------------------------

    async def _search_dmarc(self, domain: str) -> list[ModuleResult]:
        """Search for DMARC records via DDG."""
        if not _HAS_DDGS:
            return []

        results: list[ModuleResult] = []
        query = f'"_dmarc.{domain}"'
        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
            for hit in hits[:3]:
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="dmarc_record",
                        title=hit.get("title", f"DMARC record for {domain}"),
                        content=hit.get("body", None),
                        data={
                            "domain": domain,
                            "url": hit.get("href", ""),
                            "snippet": hit.get("body", ""),
                            "source": "duckduckgo_dork",
                        },
                        confidence=80,
                    )
                )
        except Exception as exc:
            self.logger.warning(f"DDG DMARC search failed: {exc}")

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
