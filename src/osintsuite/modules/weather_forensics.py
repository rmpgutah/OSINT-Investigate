"""Historical and current weather data for alibi verification."""

from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

# WMO weather code descriptions (subset)
_WEATHER_CODES: dict[int, str] = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Foggy",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snowfall",
    73: "Moderate snowfall",
    75: "Heavy snowfall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


class WeatherForensicsModule(BaseModule):
    name = "weather_forensics"
    description = "Historical and current weather data for alibi verification"

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        # Determine lat/lon from metadata, city/state, or address
        lat, lon, location_label = await self._resolve_location(target)
        if lat is None or lon is None:
            return [
                ModuleResult(
                    module_name=self.name,
                    source="weather_forensics",
                    finding_type="current_weather",
                    title="Weather lookup skipped",
                    content="No location data available for this target.",
                    data={},
                    confidence=0,
                )
            ]

        # Fetch current weather from Open-Meteo
        weather = await self._fetch_weather(lat, lon)
        if weather:
            results.append(weather)

        # General weather context
        results.append(
            ModuleResult(
                module_name=self.name,
                source="open-meteo",
                finding_type="weather_context",
                title=f"Weather context for {location_label}",
                content=(
                    f"Location: {location_label} ({lat}, {lon}). "
                    f"Weather data sourced from Open-Meteo free API. "
                    f"Useful for verifying alibis, establishing timelines, "
                    f"and cross-referencing outdoor activity claims."
                ),
                data={
                    "lat": lat,
                    "lon": lon,
                    "location": location_label,
                    "data_source": "Open-Meteo (open-meteo.com)",
                    "notes": "Free API, no key required. Hourly data available.",
                },
                confidence=60,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Location resolution
    # ------------------------------------------------------------------

    async def _resolve_location(
        self, target: Target
    ) -> tuple[float | None, float | None, str]:
        """Resolve target to (lat, lon, label). Returns (None, None, '') on failure."""
        # Check metadata_ for pre-existing lat/lon
        meta = target.metadata_ or {}
        if meta.get("lat") and meta.get("lon"):
            try:
                lat = float(meta["lat"])
                lon = float(meta["lon"])
                label = f"{target.city or ''}, {target.state or ''}".strip(", ")
                return lat, lon, label or "metadata coordinates"
            except (ValueError, TypeError):
                pass

        # Geocode city/state
        if target.city and target.state:
            lat, lon = await self._geocode(f"{target.city}, {target.state}")
            if lat is not None:
                return lat, lon, f"{target.city}, {target.state}"

        # Geocode full address
        if target.address:
            lat, lon = await self._geocode(target.address)
            if lat is not None:
                return lat, lon, target.address

        return None, None, ""

    async def _geocode(self, query_str: str) -> tuple[float | None, float | None]:
        """Geocode via Nominatim."""
        query = urllib.parse.quote(query_str)
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

    # ------------------------------------------------------------------
    # Open-Meteo weather fetch
    # ------------------------------------------------------------------

    async def _fetch_weather(
        self, lat: float, lon: float
    ) -> ModuleResult | None:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,weather_code,wind_speed_10m"
            f"&hourly=temperature_2m,precipitation"
            f"&timezone=auto"
        )
        resp = await self.fetch(url)
        if not resp:
            return None

        try:
            data = resp.json()
        except Exception:
            return None

        current = data.get("current", {})
        if not current:
            return None

        temp = current.get("temperature_2m")
        weather_code = current.get("weather_code", -1)
        wind_speed = current.get("wind_speed_10m")
        weather_desc = _WEATHER_CODES.get(weather_code, f"Code {weather_code}")

        # Extract timezone info
        tz = data.get("timezone", "Unknown")

        return ModuleResult(
            module_name=self.name,
            source="open-meteo",
            finding_type="current_weather",
            title=f"Current weather ({tz})",
            content=(
                f"Temperature: {temp}\u00b0C, "
                f"Conditions: {weather_desc}, "
                f"Wind: {wind_speed} km/h"
            ),
            data={
                "temperature": temp,
                "temperature_unit": "celsius",
                "weather_description": weather_desc,
                "weather_code": weather_code,
                "wind_speed": wind_speed,
                "wind_speed_unit": "km/h",
                "timezone": tz,
                "location": f"{lat}, {lon}",
            },
            confidence=80,
        )
