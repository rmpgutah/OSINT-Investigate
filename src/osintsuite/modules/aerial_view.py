"""Generate satellite/aerial view links for target locations."""

from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class AerialViewModule(BaseModule):
    name = "aerial_view"
    description = "Generate satellite/aerial view links for target locations"

    def applicable_target_types(self) -> list[str]:
        return ["person", "organization"]

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
                    source="aerial_view",
                    finding_type="aerial_view_link",
                    title="Aerial view skipped",
                    content="No address or city/state available for this target.",
                    data={},
                    confidence=0,
                )
            ]

        # Geocode via Nominatim to get lat/lon
        lat, lon = await self._geocode(address)
        if lat is None or lon is None:
            return [
                ModuleResult(
                    module_name=self.name,
                    source="nominatim",
                    finding_type="aerial_view_link",
                    title="Geocoding failed",
                    content=f"Could not geocode: {address}",
                    data={"address": address},
                    confidence=0,
                )
            ]

        # Generate satellite/aerial view links
        providers = self._generate_links(lat, lon)
        link_lines = []

        for provider_name, url, zoom in providers:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source=provider_name.lower().replace(" ", "_"),
                    finding_type="aerial_view_link",
                    title=f"{provider_name} aerial view",
                    content=url,
                    data={
                        "provider": provider_name,
                        "url": url,
                        "lat": lat,
                        "lon": lon,
                        "zoom": zoom,
                    },
                    confidence=85,
                )
            )
            link_lines.append(f"{provider_name}: {url}")

        # Summary with all links
        results.append(
            ModuleResult(
                module_name=self.name,
                source="aerial_view",
                finding_type="aerial_view_summary",
                title=f"Aerial views for {address}",
                content="\n".join(link_lines),
                data={
                    "address": address,
                    "lat": lat,
                    "lon": lon,
                    "provider_count": len(providers),
                },
                confidence=80,
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

    async def _geocode(self, address: str) -> tuple[float | None, float | None]:
        """Geocode an address via Nominatim, return (lat, lon) or (None, None)."""
        query = urllib.parse.quote(address)
        url = (
            f"https://nominatim.openstreetmap.org/search?q={query}"
            f"&format=json&limit=1"
        )
        resp = await self.fetch(url, headers={"User-Agent": "OSINTSuite/1.0"})
        if not resp:
            return None, None

        try:
            data = resp.json()
        except Exception:
            return None, None

        if not data:
            return None, None

        entry = data[0]
        return float(entry.get("lat", 0)), float(entry.get("lon", 0))

    @staticmethod
    def _generate_links(
        lat: float, lon: float
    ) -> list[tuple[str, str, int]]:
        """Return list of (provider_name, url, zoom) tuples."""
        return [
            (
                "Google Maps",
                f"https://www.google.com/maps/@{lat},{lon},18z/data=!3m1!1e3",
                18,
            ),
            (
                "Bing Maps",
                (
                    f"https://www.bing.com/maps?cp={lat}~{lon}"
                    f"&style=a&lvl=18"
                ),
                18,
            ),
            (
                "OpenStreetMap",
                f"https://www.openstreetmap.org/#map=18/{lat}/{lon}",
                18,
            ),
        ]
