"""Maritime vessel tracking and AIS data."""

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


class ShipTrackModule(BaseModule):
    name = "ship_track"
    description = "Maritime vessel tracking and AIS data"

    def applicable_target_types(self) -> list[str]:
        return ["organization"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        label = target.label

        # 1. Try VesselFinder demo API
        vf_results = await self._vessel_finder_search(label)
        results.extend(vf_results)

        # 2. DDG dork for vessel/ship/maritime mentions
        ddg_vessel = await self._ddg_search(
            f'"{label}" vessel OR ship OR maritime'
        )
        for h in ddg_vessel:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="vessel_mention",
                    title=h.get("title", ""),
                    content=h.get("body", ""),
                    data={
                        "title": h.get("title", ""),
                        "url": h.get("href", ""),
                        "snippet": h.get("body", ""),
                        "source": "duckduckgo_vessel_dork",
                    },
                    confidence=40,
                )
            )

        # 3. DDG dork for AIS data on tracking sites
        ais_hits = await self._ddg_search(
            f'"{label}" site:marinetraffic.com OR site:vesselfinder.com'
        )
        for h in ais_hits:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="vessel_mention",
                    title=h.get("title", ""),
                    content=h.get("body", ""),
                    data={
                        "title": h.get("title", ""),
                        "url": h.get("href", ""),
                        "snippet": h.get("body", ""),
                        "source": "ais_tracking_site",
                    },
                    confidence=55,
                )
            )

        # Summary
        results.append(
            ModuleResult(
                module_name=self.name,
                source="multiple",
                finding_type="vessel_summary",
                title=f"Maritime tracking summary for {label}: {len(results)} results",
                content=(
                    f"Searched VesselFinder API and DuckDuckGo for vessel, "
                    f"ship, and maritime references related to '{label}'."
                ),
                data={
                    "total_results": len(results),
                    "organization": label,
                },
                confidence=50,
            )
        )

        return results

    async def _vessel_finder_search(self, name: str) -> list[ModuleResult]:
        """Query VesselFinder demo API for vessel name."""
        results: list[ModuleResult] = []
        url = "https://api.vesselfinder.com/vessels"
        params = {"userkey": "demo", "name": name}

        try:
            resp = await self.fetch(url, params=params)
            if resp is None:
                return results

            data = resp.json()
            vessels = data if isinstance(data, list) else data.get("vessels", [])

            for v in vessels[:10]:
                vessel_name = v.get("name", v.get("AIS", {}).get("NAME", "Unknown"))
                mmsi = v.get("mmsi", v.get("AIS", {}).get("MMSI", ""))
                imo = v.get("imo", v.get("AIS", {}).get("IMO", ""))
                flag = v.get("flag", v.get("AIS", {}).get("FLAG", ""))

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="vesselfinder",
                        finding_type="vessel_mention",
                        title=f"Vessel: {vessel_name} (MMSI: {mmsi})",
                        content=f"Name: {vessel_name}, MMSI: {mmsi}, IMO: {imo}, Flag: {flag}",
                        data={
                            "title": vessel_name,
                            "url": f"https://www.vesselfinder.com/?mmsi={mmsi}" if mmsi else "",
                            "snippet": f"MMSI: {mmsi}, IMO: {imo}, Flag: {flag}",
                            "source": "vesselfinder_api",
                            "mmsi": str(mmsi),
                            "imo": str(imo),
                            "flag": flag,
                        },
                        confidence=65,
                    )
                )
        except Exception as e:
            self.logger.warning(f"VesselFinder API error: {e}")

        return results

    async def _ddg_search(self, query: str) -> list[dict]:
        """Run a DuckDuckGo search in a thread."""
        if not _HAS_DDGS:
            return []

        try:
            hits = await asyncio.to_thread(
                lambda: list(DDGS().text(query, max_results=10))
            )
            return hits
        except Exception as e:
            self.logger.warning(f"DDG search failed for '{query}': {e}")
            return []
