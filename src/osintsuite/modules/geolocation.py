"""Unified geolocation aggregator from IP, address, and phone data."""

from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    import phonenumbers
    from phonenumbers import geocoder as pn_geocoder

    _HAS_PHONENUMBERS = True
except ImportError:
    _HAS_PHONENUMBERS = False


class GeolocationModule(BaseModule):
    name = "geolocation"
    description = "Unified geolocation aggregator from IP, address, and phone data"

    def applicable_target_types(self) -> list[str]:
        return ["person", "domain", "ip", "phone", "organization"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        # IP-based geolocation
        if target.target_type == "ip":
            geo = await self._geolocate_ip(target.label)
            if geo:
                results.append(geo)

        # Domain — resolve to IP first, then geolocate
        if target.target_type == "domain":
            ip = await self._resolve_domain_ip(target.label)
            if ip:
                geo = await self._geolocate_ip(ip)
                if geo:
                    results.append(geo)

        # City/state geocoding (persons, organizations, etc.)
        if target.city and target.state:
            geo = await self._geocode_city_state(target.city, target.state)
            if geo:
                results.append(geo)

        # Full address geocoding
        if target.address:
            geo = await self._geocode_address(target.address)
            if geo:
                results.append(geo)

        # Phone-based region lookup
        if target.target_type == "phone" or target.phone:
            phone_str = target.label if target.target_type == "phone" else target.phone
            if phone_str:
                geo = self._geolocate_phone(phone_str)
                if geo:
                    results.append(geo)

        return results

    # ------------------------------------------------------------------
    # IP geolocation via ip-api.com
    # ------------------------------------------------------------------

    async def _geolocate_ip(self, ip: str) -> ModuleResult | None:
        url = f"http://ip-api.com/json/{ip}?fields=status,country,regionName,city,lat,lon,timezone,query"
        resp = await self.fetch(url)
        if not resp:
            return None

        try:
            data = resp.json()
        except Exception:
            return None

        if data.get("status") != "success":
            return None

        city = data.get("city", "")
        state = data.get("regionName", "")
        country = data.get("country", "")
        lat = data.get("lat")
        lon = data.get("lon")

        return ModuleResult(
            module_name=self.name,
            source="ip-api.com",
            finding_type="geolocation",
            title=f"IP Geolocation: {ip}",
            content=f"{city}, {state}, {country} ({lat}, {lon})",
            data={
                "lat": lat,
                "lon": lon,
                "city": city,
                "state": state,
                "country": country,
                "source": "ip-api.com",
                "accuracy": "city-level",
            },
            confidence=70,
        )

    # ------------------------------------------------------------------
    # Domain → IP resolution
    # ------------------------------------------------------------------

    async def _resolve_domain_ip(self, domain: str) -> str | None:
        """Resolve a domain to an IP via ip-api.com (which accepts domains)."""
        if "://" in domain:
            domain = domain.split("://")[1].split("/")[0]
        url = f"http://ip-api.com/json/{domain}?fields=status,query"
        resp = await self.fetch(url)
        if not resp:
            return None
        try:
            data = resp.json()
            if data.get("status") == "success":
                return data.get("query")
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Nominatim geocoding (city/state)
    # ------------------------------------------------------------------

    async def _geocode_city_state(self, city: str, state: str) -> ModuleResult | None:
        query = urllib.parse.quote(f"{city}, {state}")
        url = (
            f"https://nominatim.openstreetmap.org/search?q={query}"
            f"&format=json&limit=1"
        )
        resp = await self.fetch(url, headers={"User-Agent": "OSINTSuite/1.0"})
        if not resp:
            return None

        try:
            data = resp.json()
        except Exception:
            return None

        if not data:
            return None

        entry = data[0]
        lat = float(entry.get("lat", 0))
        lon = float(entry.get("lon", 0))

        return ModuleResult(
            module_name=self.name,
            source="nominatim",
            finding_type="geolocation",
            title=f"Geocoded: {city}, {state}",
            content=f"{city}, {state} ({lat}, {lon})",
            data={
                "lat": lat,
                "lon": lon,
                "city": city,
                "state": state,
                "country": "US",
                "source": "nominatim",
                "accuracy": "city-level",
            },
            confidence=75,
        )

    # ------------------------------------------------------------------
    # Nominatim geocoding (full address)
    # ------------------------------------------------------------------

    async def _geocode_address(self, address: str) -> ModuleResult | None:
        query = urllib.parse.quote(address)
        url = (
            f"https://nominatim.openstreetmap.org/search?q={query}"
            f"&format=json&limit=1"
        )
        resp = await self.fetch(url, headers={"User-Agent": "OSINTSuite/1.0"})
        if not resp:
            return None

        try:
            data = resp.json()
        except Exception:
            return None

        if not data:
            return None

        entry = data[0]
        lat = float(entry.get("lat", 0))
        lon = float(entry.get("lon", 0))

        return ModuleResult(
            module_name=self.name,
            source="nominatim",
            finding_type="geolocation",
            title=f"Geocoded address",
            content=f"{address} ({lat}, {lon})",
            data={
                "lat": lat,
                "lon": lon,
                "city": "",
                "state": "",
                "country": "",
                "source": "nominatim",
                "accuracy": "address-level",
            },
            confidence=80,
        )

    # ------------------------------------------------------------------
    # Phone number region via phonenumbers library
    # ------------------------------------------------------------------

    def _geolocate_phone(self, phone_str: str) -> ModuleResult | None:
        if not _HAS_PHONENUMBERS:
            self.logger.debug("phonenumbers library not installed — skipping phone geolocation")
            return None

        try:
            parsed = phonenumbers.parse(phone_str, "US")
            region = pn_geocoder.description_for_number(parsed, "en")
            country_code = phonenumbers.region_code_for_number(parsed)

            if not region and not country_code:
                return None

            return ModuleResult(
                module_name=self.name,
                source="phonenumbers",
                finding_type="geolocation",
                title=f"Phone region: {phone_str}",
                content=f"{region}, {country_code}",
                data={
                    "lat": None,
                    "lon": None,
                    "city": "",
                    "state": region or "",
                    "country": country_code or "",
                    "source": "phonenumbers",
                    "accuracy": "region-level",
                },
                confidence=60,
            )
        except Exception as e:
            self.logger.debug(f"Phone geolocation failed for {phone_str}: {e}")
            return None
