"""WiFi network lookup module — WiGLE geolocation and SSID discovery."""

from __future__ import annotations

import re
import urllib.parse
from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class WifiLookupModule(BaseModule):
    name = "wifi_lookup"
    description = "WiFi network geolocation via WiGLE"

    def __init__(self, *args, wigle_api_key: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.wigle_api_key = wigle_api_key

    def applicable_target_types(self) -> list[str]:
        return ["person", "organization"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        label = target.label

        # Source 1: WiGLE API search by SSID (requires API key)
        if self.wigle_api_key:
            results.extend(await self._wigle_ssid_search(label))

            # If target has location info, also search by bounding box
            if target.city or target.address:
                results.extend(await self._wigle_location_search(target))
        else:
            self.logger.info(
                "WiGLE API key not configured; skipping WiGLE search. "
                "Set wigle_api_key for WiFi geolocation."
            )

        # Source 2: DDG dork for WiFi/SSID references
        results.extend(await self._dork_wifi(label, target.city))

        # Summary
        wifi_results = [r for r in results if r.finding_type == "wifi_network"]
        mention_results = [r for r in results if r.finding_type == "wifi_mention"]
        results.append(
            ModuleResult(
                module_name=self.name,
                source="wifi_lookup",
                finding_type="wifi_summary",
                title=f"WiFi lookup summary for {label}",
                content=(
                    f"Found {len(wifi_results)} WiFi network(s) and "
                    f"{len(mention_results)} mention(s) from web search."
                ),
                data={
                    "label": label,
                    "wifi_networks_found": len(wifi_results),
                    "web_mentions": len(mention_results),
                    "wigle_available": bool(self.wigle_api_key),
                },
                confidence=60,
            )
        )

        return results

    async def _wigle_ssid_search(self, ssid_query: str) -> list[ModuleResult]:
        """Search WiGLE API for networks matching the SSID query."""
        results: list[ModuleResult] = []
        encoded_ssid = urllib.parse.quote(ssid_query)
        url = f"https://api.wigle.net/api/v2/network/search?ssid={encoded_ssid}"

        response = await self.fetch(
            url,
            headers={
                "Authorization": f"Basic {self.wigle_api_key}",
                "Accept": "application/json",
            },
        )
        if not response:
            return results

        try:
            data = response.json()
            if not data.get("success"):
                self.logger.info(f"WiGLE search returned no results for {ssid_query}")
                return results

            for network in data.get("results", [])[:20]:
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="wigle",
                        finding_type="wifi_network",
                        title=f"WiFi: {network.get('ssid', 'Unknown')}",
                        content=(
                            f"SSID: {network.get('ssid')}, "
                            f"BSSID: {network.get('netid')}, "
                            f"Encryption: {network.get('encryption')}, "
                            f"Channel: {network.get('channel')}, "
                            f"Location: ({network.get('trilat')}, {network.get('trilong')})"
                        ),
                        data={
                            "ssid": network.get("ssid"),
                            "bssid": network.get("netid"),
                            "encryption": network.get("encryption"),
                            "channel": network.get("channel"),
                            "lat": network.get("trilat"),
                            "lon": network.get("trilong"),
                            "city": network.get("city"),
                            "region": network.get("region"),
                            "country": network.get("country"),
                            "first_seen": network.get("firsttime"),
                            "last_seen": network.get("lasttime"),
                        },
                        confidence=75,
                    )
                )
        except Exception as e:
            self.logger.warning(f"WiGLE SSID search parsing failed: {e}")

        return results

    async def _wigle_location_search(self, target: Target) -> list[ModuleResult]:
        """Search WiGLE by approximate bounding box around target location."""
        results: list[ModuleResult] = []

        # Build a location query from city/address
        location_parts = []
        if target.address:
            location_parts.append(target.address)
        if target.city:
            location_parts.append(target.city)
        if target.state:
            location_parts.append(target.state)

        if not location_parts:
            return results

        # Use city-level search (WiGLE search by city/region)
        params = {}
        if target.city:
            params["city"] = target.city
        if target.state:
            params["region"] = target.state

        if not params:
            return results

        query_str = urllib.parse.urlencode(params)
        url = f"https://api.wigle.net/api/v2/network/search?{query_str}&resultsPerPage=10"

        response = await self.fetch(
            url,
            headers={
                "Authorization": f"Basic {self.wigle_api_key}",
                "Accept": "application/json",
            },
        )
        if not response:
            return results

        try:
            data = response.json()
            if not data.get("success"):
                return results

            for network in data.get("results", [])[:10]:
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="wigle_location",
                        finding_type="wifi_network",
                        title=f"WiFi near {target.city or 'location'}: {network.get('ssid', 'Hidden')}",
                        content=(
                            f"SSID: {network.get('ssid')}, "
                            f"BSSID: {network.get('netid')}, "
                            f"Location: ({network.get('trilat')}, {network.get('trilong')})"
                        ),
                        data={
                            "ssid": network.get("ssid"),
                            "bssid": network.get("netid"),
                            "encryption": network.get("encryption"),
                            "channel": network.get("channel"),
                            "lat": network.get("trilat"),
                            "lon": network.get("trilong"),
                            "search_type": "location",
                        },
                        confidence=60,
                    )
                )
        except Exception as e:
            self.logger.warning(f"WiGLE location search failed: {e}")

        return results

    async def _dork_wifi(self, label: str, city: str | None) -> list[ModuleResult]:
        """Search DuckDuckGo for WiFi/SSID references related to the target."""
        results: list[ModuleResult] = []

        query_parts = [f'"{label}"', "wifi ssid OR \"wireless network\""]
        if city:
            query_parts.append(f'"{city}"')

        query = urllib.parse.quote_plus(" ".join(query_parts))
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
            link_pattern = re.compile(
                r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                re.DOTALL | re.IGNORECASE,
            )
            snippet_pattern = re.compile(
                r'class="result__snippet"[^>]*>(.*?)</(?:a|span|td)',
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
                            finding_type="wifi_mention",
                            title=f"WiFi mention: {clean_title[:80]}",
                            content=clean_snippet[:300] if clean_snippet else clean_title,
                            data={
                                "url": href,
                                "title": clean_title,
                                "snippet": clean_snippet[:500],
                                "label": label,
                            },
                            confidence=30,
                        )
                    )
        except Exception as e:
            self.logger.warning(f"DDG WiFi dork parsing failed: {e}")

        return results
