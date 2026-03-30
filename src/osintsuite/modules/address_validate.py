"""Address validation and geocoding via USPS and OpenStreetMap."""

from __future__ import annotations

import asyncio
import urllib.parse
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    from duckduckgo_search import DDGS

    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False


class AddressValidateModule(BaseModule):
    name = "address_validate"
    description = "Address validation and geocoding via USPS and OpenStreetMap"

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        address = self._build_address(target)
        if not address:
            return [
                ModuleResult(
                    module_name=self.name,
                    source="address_validate",
                    finding_type="address_geocode",
                    title="Address validation skipped",
                    content="No address or city/state available for this target.",
                    data={},
                    confidence=0,
                )
            ]

        # Nominatim geocode
        geocode_results = await self._geocode_address(address)
        results.extend(geocode_results)

        # DDG dork for property/resident info
        dork_results = await self._address_dork(address)
        results.extend(dork_results)

        # Summary
        geocoded_count = len(geocode_results)
        dork_count = len(dork_results)
        results.append(
            ModuleResult(
                module_name=self.name,
                source="address_validate",
                finding_type="address_summary",
                title=f"Address validation summary for {address}",
                content=(
                    f"Geocoded {geocoded_count} result(s), "
                    f"found {dork_count} search result(s)."
                ),
                data={
                    "address_queried": address,
                    "geocode_results": geocoded_count,
                    "search_results": dork_count,
                },
                confidence=55,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_address(target: Target) -> str:
        """Build the best address string from available target fields."""
        if target.address:
            return target.address
        parts = []
        if target.city:
            parts.append(target.city)
        if target.state:
            parts.append(target.state)
        return ", ".join(parts) if parts else ""

    # ------------------------------------------------------------------
    # Nominatim geocoding with address details
    # ------------------------------------------------------------------

    async def _geocode_address(self, address: str) -> list[ModuleResult]:
        query = urllib.parse.quote(address)
        url = (
            f"https://nominatim.openstreetmap.org/search?q={query}"
            f"&format=json&addressdetails=1&limit=3"
        )
        resp = await self.fetch(url, headers={"User-Agent": "OSINTSuite/1.0"})
        if not resp:
            return []

        try:
            data = resp.json()
        except Exception:
            return []

        if not data:
            return []

        results: list[ModuleResult] = []
        for entry in data:
            lat = float(entry.get("lat", 0))
            lon = float(entry.get("lon", 0))
            display = entry.get("display_name", address)
            addr_detail = entry.get("address", {})

            components = {
                "house_number": addr_detail.get("house_number", ""),
                "road": addr_detail.get("road", ""),
                "city": (
                    addr_detail.get("city")
                    or addr_detail.get("town")
                    or addr_detail.get("village", "")
                ),
                "state": addr_detail.get("state", ""),
                "postcode": addr_detail.get("postcode", ""),
                "country": addr_detail.get("country", ""),
            }

            formatted = ", ".join(
                v for v in [
                    f"{components['house_number']} {components['road']}".strip(),
                    components["city"],
                    components["state"],
                    components["postcode"],
                    components["country"],
                ]
                if v
            )

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="nominatim",
                    finding_type="address_geocode",
                    title=f"Geocoded: {formatted or display}",
                    content=f"{formatted} ({lat}, {lon})",
                    data={
                        "formatted_address": formatted or display,
                        "lat": lat,
                        "lon": lon,
                        "components": components,
                    },
                    confidence=75,
                )
            )

        return results

    # ------------------------------------------------------------------
    # DDG dork for property / resident info
    # ------------------------------------------------------------------

    async def _address_dork(self, address: str) -> list[ModuleResult]:
        if not _HAS_DDGS:
            self.logger.debug(
                "duckduckgo_search not installed — skipping address dork"
            )
            return []

        query = f'"{address}" "property" OR "resident" OR "owner"'
        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
        except Exception as exc:
            self.logger.warning(f"Address dork search failed: {exc}")
            return []

        results: list[ModuleResult] = []
        for hit in hits[:5]:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="address_search_result",
                    title=hit.get("title", "Search result"),
                    content=hit.get("body", ""),
                    data={
                        "url": hit.get("href", ""),
                        "title": hit.get("title", ""),
                        "snippet": hit.get("body", ""),
                        "query": query,
                    },
                    confidence=45,
                )
            )

        return results

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=5))
