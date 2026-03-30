"""DNS history module — historical DNS records and domain changes."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class DnsHistoryModule(BaseModule):
    name = "dns_history"
    description = "Historical DNS records and domain changes"

    def applicable_target_types(self) -> list[str]:
        return ["domain"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        domain = target.label

        # Strip protocol if present
        if "://" in domain:
            domain = domain.split("://")[1].split("/")[0]

        # Source 1: HackerTarget host search (free, no key)
        results.extend(await self._hackertarget_hosts(domain))

        # Source 2: ViewDNS.info history lookup
        results.extend(await self._viewdns_history(domain))

        # Source 3: DDG dork for DNS history
        results.extend(await self._dork_dns_history(domain))

        # Summary
        record_results = [
            r for r in results if r.finding_type == "dns_record_history"
        ]
        results.append(
            ModuleResult(
                module_name=self.name,
                source="dns_history",
                finding_type="dns_history_summary",
                title=f"DNS history summary for {domain}",
                content=f"Found {len(record_results)} historical DNS record(s) across all sources.",
                data={
                    "domain": domain,
                    "total_records": len(record_results),
                    "sources_checked": ["hackertarget", "viewdns", "dork"],
                },
                confidence=70,
            )
        )

        return results

    async def _hackertarget_hosts(self, domain: str) -> list[ModuleResult]:
        """Query HackerTarget free API for subdomains and their IPs."""
        results: list[ModuleResult] = []
        url = f"https://api.hackertarget.com/hostsearch/?q={domain}"

        response = await self.fetch(url)
        if not response:
            return results

        text = response.text.strip()
        if not text or "error" in text.lower() or "API count exceeded" in text:
            self.logger.info(f"HackerTarget returned no data for {domain}")
            return results

        seen: set[str] = set()
        for line in text.splitlines():
            parts = line.strip().split(",")
            if len(parts) >= 2:
                hostname = parts[0].strip()
                ip = parts[1].strip()
                key = f"{hostname}:{ip}"
                if key in seen:
                    continue
                seen.add(key)

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="hackertarget",
                        finding_type="dns_record_history",
                        title=f"DNS record: {hostname} -> {ip}",
                        content=f"{hostname} resolves to {ip}",
                        data={
                            "domain": domain,
                            "hostname": hostname,
                            "ip": ip,
                            "record_type": "A",
                            "source": "hackertarget",
                        },
                        confidence=75,
                    )
                )

        return results

    async def _viewdns_history(self, domain: str) -> list[ModuleResult]:
        """Scrape ViewDNS.info IP history page for historical DNS data."""
        results: list[ModuleResult] = []
        url = f"https://viewdns.info/iphistory/?domain={domain}"

        response = await self.fetch(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36",
            },
        )
        if not response:
            return results

        try:
            # Parse table rows from the IP history page
            text = response.text
            # Look for IP history table rows: <td>IP</td><td>...</td><td>Date</td>
            row_pattern = re.compile(
                r"<tr>\s*<td>([^<]+)</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>",
                re.IGNORECASE,
            )
            matches = row_pattern.findall(text)
            for match in matches:
                ip_addr = match[0].strip()
                location = match[1].strip()
                owner = match[2].strip()
                last_seen = match[3].strip()

                # Skip header row
                if ip_addr.lower() in ("ip address", "ip"):
                    continue

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="viewdns",
                        finding_type="dns_record_history",
                        title=f"Historical IP: {domain} -> {ip_addr} ({last_seen})",
                        content=f"{domain} pointed to {ip_addr} (owner: {owner}, location: {location}, last seen: {last_seen})",
                        data={
                            "domain": domain,
                            "ip": ip_addr,
                            "location": location,
                            "owner": owner,
                            "last_seen": last_seen,
                            "source": "viewdns",
                        },
                        confidence=70,
                    )
                )
        except Exception as e:
            self.logger.warning(f"ViewDNS parsing failed for {domain}: {e}")

        return results

    async def _dork_dns_history(self, domain: str) -> list[ModuleResult]:
        """Search DuckDuckGo for DNS history references."""
        results: list[ModuleResult] = []
        import urllib.parse

        query = urllib.parse.quote_plus(f'"dns history" "{domain}"')
        url = f"https://html.duckduckgo.com/html/?q={query}"

        response = await self.fetch(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36",
            },
        )
        if not response:
            return results

        try:
            # Extract result snippets
            snippet_pattern = re.compile(
                r'class="result__snippet"[^>]*>(.*?)</(?:a|span|td)',
                re.DOTALL | re.IGNORECASE,
            )
            link_pattern = re.compile(
                r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                re.DOTALL | re.IGNORECASE,
            )

            links = link_pattern.findall(response.text)
            snippets = snippet_pattern.findall(response.text)

            for i, (href, title) in enumerate(links[:5]):
                clean_title = re.sub(r"<[^>]+>", "", title).strip()
                clean_snippet = ""
                if i < len(snippets):
                    clean_snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()

                if clean_title:
                    results.append(
                        ModuleResult(
                            module_name=self.name,
                            source="dork_ddg",
                            finding_type="dns_record_history",
                            title=f"DNS history ref: {clean_title[:80]}",
                            content=clean_snippet[:300] if clean_snippet else clean_title,
                            data={
                                "domain": domain,
                                "url": href,
                                "title": clean_title,
                                "snippet": clean_snippet[:500],
                                "source": "dork",
                            },
                            confidence=40,
                        )
                    )
        except Exception as e:
            self.logger.warning(f"DDG dork parsing failed for {domain}: {e}")

        return results
