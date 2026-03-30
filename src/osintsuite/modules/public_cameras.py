"""Public camera and webcam discovery near target locations."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    from duckduckgo_search import DDGS

    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False


class PublicCamerasModule(BaseModule):
    name = "public_cameras"
    description = "Public camera and webcam discovery near target locations"

    def applicable_target_types(self) -> list[str]:
        return ["organization", "ip"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        label = target.label

        if target.target_type == "ip":
            # For IP targets: search for camera services via Shodan-like dork
            results.extend(await self._ip_camera_search(label))
        else:
            # For organization targets: search by name + location
            city = target.city or ""
            state = target.state or ""
            results.extend(await self._location_camera_search(label, city, state))

        # Summary
        camera_count = sum(
            1 for r in results if r.finding_type == "public_camera"
        )
        results.append(
            ModuleResult(
                module_name=self.name,
                source="multiple",
                finding_type="camera_summary",
                title=f"Public camera search for {label}: {camera_count} results",
                content=f"Searched for public cameras and webcams related to '{label}'.",
                data={
                    "total_cameras_found": camera_count,
                    "target": label,
                    "target_type": target.target_type,
                },
                confidence=45,
            )
        )

        return results

    async def _ip_camera_search(self, ip: str) -> list[ModuleResult]:
        """Search for camera services on an IP via DDG Shodan-style dork."""
        results: list[ModuleResult] = []
        if not _HAS_DDGS:
            return results

        # Common camera ports dork
        queries = [
            f'"{ip}" webcam OR camera OR CCTV OR RTSP',
            f'"{ip}" site:shodan.io camera OR webcam OR port:554 OR port:8080',
        ]

        for query in queries:
            try:
                hits = await asyncio.to_thread(
                    lambda q=query: list(DDGS().text(q, max_results=5))
                )
                for h in hits:
                    results.append(
                        ModuleResult(
                            module_name=self.name,
                            source="duckduckgo",
                            finding_type="public_camera",
                            title=h.get("title", ""),
                            content=h.get("body", ""),
                            data={
                                "title": h.get("title", ""),
                                "url": h.get("href", ""),
                                "snippet": h.get("body", ""),
                                "source": "shodan_dork",
                                "location_hint": ip,
                            },
                            confidence=40,
                        )
                    )
            except Exception as e:
                self.logger.warning(f"DDG IP camera search failed: {e}")

            await asyncio.sleep(2)

        return results

    async def _location_camera_search(
        self, label: str, city: str, state: str
    ) -> list[ModuleResult]:
        """Search for public cameras near an organization's location."""
        results: list[ModuleResult] = []
        if not _HAS_DDGS:
            return results

        location_str = " ".join(filter(None, [city, state]))

        queries = [
            f'"{label}" webcam OR camera OR CCTV {location_str}'.strip(),
        ]

        # Search insecam.org for city/state
        if city or state:
            insecam_query = f"site:insecam.org {city or state}"
            queries.append(insecam_query)

        for idx, query in enumerate(queries):
            if idx > 0:
                await asyncio.sleep(2)

            try:
                hits = await asyncio.to_thread(
                    lambda q=query: list(DDGS().text(q, max_results=10))
                )
                for h in hits:
                    loc_hint = location_str if location_str else label
                    results.append(
                        ModuleResult(
                            module_name=self.name,
                            source="duckduckgo",
                            finding_type="public_camera",
                            title=h.get("title", ""),
                            content=h.get("body", ""),
                            data={
                                "title": h.get("title", ""),
                                "url": h.get("href", ""),
                                "snippet": h.get("body", ""),
                                "source": "insecam_dork" if "insecam" in query else "camera_dork",
                                "location_hint": loc_hint,
                            },
                            confidence=35,
                        )
                    )
            except Exception as e:
                self.logger.warning(f"DDG camera search failed for '{query}': {e}")

        return results
