"""Aircraft tracking and flight history via OpenSky Network."""

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

# Simple geocoding fallback: major US cities
_CITY_COORDS: dict[str, tuple[float, float]] = {
    "new york": (40.7128, -74.0060),
    "los angeles": (34.0522, -118.2437),
    "chicago": (41.8781, -87.6298),
    "houston": (29.7604, -95.3698),
    "phoenix": (33.4484, -112.0740),
    "philadelphia": (39.9526, -75.1652),
    "san antonio": (29.4241, -98.4936),
    "san diego": (32.7157, -117.1611),
    "dallas": (32.7767, -96.7970),
    "san jose": (37.3382, -121.8863),
    "austin": (30.2672, -97.7431),
    "jacksonville": (30.3322, -81.6557),
    "san francisco": (37.7749, -122.4194),
    "seattle": (47.6062, -122.3321),
    "denver": (39.7392, -104.9903),
    "washington": (38.9072, -77.0369),
    "miami": (25.7617, -80.1918),
    "atlanta": (33.7490, -84.3880),
    "boston": (42.3601, -71.0589),
    "las vegas": (36.1699, -115.1398),
    "portland": (45.5152, -122.6784),
    "detroit": (42.3314, -83.0458),
    "minneapolis": (44.9778, -93.2650),
    "tampa": (27.9506, -82.4572),
    "orlando": (28.5383, -81.3792),
    "st louis": (38.6270, -90.1994),
    "pittsburgh": (40.4406, -79.9959),
    "charlotte": (35.2271, -80.8431),
    "salt lake city": (40.7608, -111.8910),
    "nashville": (36.1627, -86.7816),
}


class FlightTrackModule(BaseModule):
    name = "flight_track"
    description = "Aircraft tracking and flight history via OpenSky Network"

    def applicable_target_types(self) -> list[str]:
        return ["organization"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        coords = self._geocode(target)
        if not coords:
            # Fallback: try DDG search for org + aviation/flights
            results.extend(await self._dork_search(target))
            return results

        lat, lon = coords
        aircraft = await self._fetch_opensky(lat, lon)

        if aircraft is None:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="opensky",
                    finding_type="aircraft_nearby",
                    title=f"OpenSky API unavailable for {target.label}",
                    content="Could not retrieve aircraft data from OpenSky Network.",
                    data={"error": "api_unavailable", "lat": lat, "lon": lon},
                    confidence=10,
                )
            )
            return results

        count = 0
        for state in aircraft[:20]:
            count += 1
            icao24 = state[0] or ""
            callsign = (state[1] or "").strip()
            origin_country = state[2] or ""
            a_lon = state[5]
            a_lat = state[6]
            baro_alt = state[7]
            velocity = state[9]
            on_ground = state[8]

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="opensky",
                    finding_type="aircraft_nearby",
                    title=f"Aircraft {callsign or icao24} ({origin_country})",
                    content=(
                        f"ICAO24: {icao24}, Callsign: {callsign}, "
                        f"Origin: {origin_country}, "
                        f"Altitude: {baro_alt}m, Velocity: {velocity}m/s"
                    ),
                    data={
                        "icao24": icao24,
                        "callsign": callsign,
                        "origin_country": origin_country,
                        "lat": a_lat,
                        "lon": a_lon,
                        "altitude": baro_alt,
                        "velocity": velocity,
                        "on_ground": on_ground,
                    },
                    confidence=70,
                )
            )

        # Summary
        results.append(
            ModuleResult(
                module_name=self.name,
                source="opensky",
                finding_type="aircraft_summary",
                title=f"Flight tracking summary near {target.label}: {count} aircraft detected",
                content=(
                    f"Searched 1-degree bounding box around ({lat:.4f}, {lon:.4f}). "
                    f"Found {count} aircraft in the area."
                ),
                data={
                    "total_aircraft": count,
                    "search_lat": lat,
                    "search_lon": lon,
                    "bounding_box": {
                        "lamin": lat - 1,
                        "lamax": lat + 1,
                        "lomin": lon - 1,
                        "lomax": lon + 1,
                    },
                },
                confidence=65,
            )
        )

        return results

    def _geocode(self, target: Target) -> tuple[float, float] | None:
        """Attempt to geocode target city/state to lat/lon."""
        city = (target.city or "").strip().lower()
        if city and city in _CITY_COORDS:
            return _CITY_COORDS[city]

        # Try metadata for explicit coordinates
        meta = target.metadata_ or {}
        lat = meta.get("lat") or meta.get("latitude")
        lon = meta.get("lon") or meta.get("longitude")
        if lat is not None and lon is not None:
            try:
                return (float(lat), float(lon))
            except (ValueError, TypeError):
                pass

        return None

    async def _fetch_opensky(
        self, lat: float, lon: float
    ) -> list[list[Any]] | None:
        """Query OpenSky Network for aircraft in a 1-degree bounding box."""
        url = "https://opensky-network.org/api/states/all"
        params = {
            "lamin": lat - 1,
            "lomin": lon - 1,
            "lamax": lat + 1,
            "lomax": lon + 1,
        }
        try:
            resp = await self.fetch(url, params=params)
            if resp is None:
                return None
            data = resp.json()
            return data.get("states") or []
        except Exception as e:
            self.logger.warning(f"OpenSky API error: {e}")
            return None

    async def _dork_search(self, target: Target) -> list[ModuleResult]:
        """Fallback DDG search for aviation mentions."""
        results: list[ModuleResult] = []
        if not _HAS_DDGS:
            return results

        query = f'"{target.label}" aircraft OR flight OR aviation'
        try:
            hits = await asyncio.to_thread(
                lambda: list(DDGS().text(query, max_results=5))
            )
            for h in hits:
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="aircraft_mention",
                        title=h.get("title", ""),
                        content=h.get("body", ""),
                        data={
                            "url": h.get("href", ""),
                            "title": h.get("title", ""),
                            "snippet": h.get("body", ""),
                        },
                        confidence=30,
                    )
                )
        except Exception as e:
            self.logger.warning(f"DDG aviation search failed: {e}")

        return results
