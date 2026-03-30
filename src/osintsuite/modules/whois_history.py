"""WHOIS history module -- RDAP lookup and historical WHOIS dork searches."""

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


class WhoisHistoryModule(BaseModule):
    name = "whois_history"
    description = "Current and historical WHOIS / RDAP domain registration data"

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
            self.logger.info("No domain available on target, skipping WHOIS history")
            return results

        # 1. Current WHOIS via RDAP
        results.extend(await self._query_rdap(domain))

        # 2. Historical WHOIS via DDG dork searches
        results.extend(await self._search_historical_whois(domain))

        # Summary
        results.append(
            ModuleResult(
                module_name=self.name,
                source="whois_history",
                finding_type="whois_summary",
                title=f"WHOIS history summary for {domain}",
                content=(
                    f"Found {len(results)} WHOIS-related finding(s) for "
                    f'"{domain}" via RDAP and historical dork searches.'
                ),
                data={
                    "domain": domain,
                    "total_findings": len(results),
                },
                confidence=65,
            )
        )

        return results

    # ------------------------------------------------------------------
    # RDAP lookup
    # ------------------------------------------------------------------

    async def _query_rdap(self, domain: str) -> list[ModuleResult]:
        """Fetch current WHOIS data via RDAP protocol."""
        results: list[ModuleResult] = []
        url = self.RDAP_URL.format(domain=domain)

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"RDAP request failed: {exc}")
            return results

        if not response:
            self.logger.info("RDAP returned no response")
            return results

        try:
            data: dict[str, Any] = response.json()
        except Exception:
            self.logger.warning("Failed to parse RDAP JSON response")
            return results

        # Extract registration info
        registrant = self._extract_entity(data, "registrant")
        registrar = self._extract_entity(data, "registrar")

        # Events (creation, expiration, last changed)
        events = {}
        for event in data.get("events", []):
            action = event.get("eventAction", "")
            date = event.get("eventDate", "")
            if action and date:
                events[action] = date

        creation_date = events.get("registration", "")
        expiration_date = events.get("expiration", "")
        last_changed = events.get("last changed", events.get("lastChanged", ""))

        # Nameservers
        nameservers = []
        for ns in data.get("nameservers", []):
            ns_name = ns.get("ldhName", ns.get("objectClassName", ""))
            if ns_name:
                nameservers.append(ns_name)

        # Privacy detection
        privacy_protected = False
        raw_registrant = str(registrant).lower()
        privacy_keywords = ["privacy", "whoisguard", "redacted", "proxy", "withheld", "protected"]
        if any(kw in raw_registrant for kw in privacy_keywords):
            privacy_protected = True

        # Status codes
        status_list = data.get("status", [])

        results.append(
            ModuleResult(
                module_name=self.name,
                source="rdap",
                finding_type="whois_current",
                title=f"Current WHOIS record for {domain}",
                content=(
                    f"Domain: {domain}, Registrar: {registrar}, "
                    f"Created: {creation_date}, Expires: {expiration_date}, "
                    f"Nameservers: {', '.join(nameservers) or 'N/A'}, "
                    f"Privacy: {'Yes' if privacy_protected else 'No'}"
                ),
                data={
                    "domain": domain,
                    "registrant": registrant,
                    "registrar": registrar,
                    "creation_date": creation_date,
                    "expiration_date": expiration_date,
                    "last_changed": last_changed,
                    "name_servers": nameservers,
                    "privacy_protected": privacy_protected,
                    "status": status_list,
                    "handle": data.get("handle", ""),
                },
                confidence=85,
            )
        )

        self.logger.info(f"RDAP returned WHOIS data for '{domain}'")
        return results

    # ------------------------------------------------------------------
    # Historical WHOIS dork searches
    # ------------------------------------------------------------------

    async def _search_historical_whois(self, domain: str) -> list[ModuleResult]:
        """Search for historical WHOIS data via DuckDuckGo dork queries."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed -- skipping historical WHOIS searches"
            )
            return []

        queries = [
            f'site:whoisology.com "{domain}"',
            f'"{domain}" registrant OR "admin contact"',
            f'"{domain}" whois history OR "registration changed"',
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

                # Detect change indicators
                change_type = self._detect_change_type(title, snippet)

                all_results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="whois_historical",
                        title=title or f"Historical WHOIS result for {domain}",
                        content=snippet or None,
                        data={
                            "domain": domain,
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "duckduckgo_dork",
                            "change_type": change_type,
                        },
                        confidence=55,
                    )
                )

        self.logger.info(
            f"DDG dork searches found {len(all_results)} historical WHOIS results for '{domain}'"
        )
        return all_results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_entity(data: dict[str, Any], role: str) -> str:
        """Extract entity name by role from RDAP response."""
        for entity in data.get("entities", []):
            roles = entity.get("roles", [])
            if role in roles:
                vcard = entity.get("vcardArray", [])
                if isinstance(vcard, list) and len(vcard) > 1:
                    for field in vcard[1]:
                        if isinstance(field, list) and len(field) >= 4:
                            if field[0] == "fn":
                                return str(field[3])
                # Fallback to handle
                return entity.get("handle", "Unknown")
        return "Unknown"

    @staticmethod
    def _detect_change_type(title: str, snippet: str) -> str:
        """Detect the type of WHOIS change from content clues."""
        combined = f"{title} {snippet}".lower()
        if "registrar" in combined and ("change" in combined or "transfer" in combined):
            return "registrar_change"
        if "nameserver" in combined and ("change" in combined or "update" in combined):
            return "nameserver_change"
        if "privacy" in combined:
            return "privacy_toggle"
        if "registrant" in combined and ("change" in combined or "update" in combined):
            return "registrant_change"
        return "general"

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
