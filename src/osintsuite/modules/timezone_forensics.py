"""Timezone analysis and timestamp cross-referencing."""

from __future__ import annotations

import urllib.parse
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    import phonenumbers
    from phonenumbers.timezone import time_zones_for_number

    _HAS_PHONENUMBERS = True
except ImportError:
    _HAS_PHONENUMBERS = False

try:
    from timezonefinder import TimezoneFinder

    _HAS_TZFINDER = True
except ImportError:
    _HAS_TZFINDER = False

try:
    import zoneinfo

    _HAS_ZONEINFO = True
except ImportError:
    _HAS_ZONEINFO = False


class TimezoneForensicsModule(BaseModule):
    name = "timezone_forensics"
    description = "Timezone analysis and timestamp cross-referencing"

    def applicable_target_types(self) -> list[str]:
        return ["person", "domain", "ip", "phone", "organization"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        timezones_found: list[dict] = []

        # IP-based timezone
        if target.target_type == "ip":
            tz_result = await self._tz_from_ip(target.label)
            if tz_result:
                results.append(tz_result)
                timezones_found.append(tz_result.data)

        # Domain — resolve IP first
        if target.target_type == "domain":
            ip = await self._resolve_domain_ip(target.label)
            if ip:
                tz_result = await self._tz_from_ip(ip)
                if tz_result:
                    results.append(tz_result)
                    timezones_found.append(tz_result.data)

        # Phone-based timezone
        if target.target_type == "phone" or target.phone:
            phone_str = target.label if target.target_type == "phone" else target.phone
            if phone_str:
                tz_result = self._tz_from_phone(phone_str)
                if tz_result:
                    results.append(tz_result)
                    timezones_found.append(tz_result.data)

        # City/state-based timezone
        if target.city and target.state:
            tz_result = await self._tz_from_city_state(target.city, target.state)
            if tz_result:
                results.append(tz_result)
                timezones_found.append(tz_result.data)

        # Check for discrepancies among sources
        if len(timezones_found) > 1:
            unique_tzs = set()
            for tz_data in timezones_found:
                tz_val = tz_data.get("timezone", "")
                if tz_val:
                    unique_tzs.add(tz_val)

            if len(unique_tzs) > 1:
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="timezone_forensics",
                        finding_type="timezone_discrepancy",
                        title="Timezone discrepancy detected",
                        content=(
                            f"Multiple sources disagree on timezone: "
                            f"{', '.join(sorted(unique_tzs))}"
                        ),
                        data={
                            "timezones_found": sorted(unique_tzs),
                            "source_count": len(timezones_found),
                            "sources": [
                                d.get("source", "unknown") for d in timezones_found
                            ],
                        },
                        confidence=70,
                    )
                )

        return results

    # ------------------------------------------------------------------
    # IP → timezone via ip-api.com
    # ------------------------------------------------------------------

    async def _tz_from_ip(self, ip: str) -> ModuleResult | None:
        url = f"http://ip-api.com/json/{ip}?fields=status,timezone,query"
        resp = await self.fetch(url)
        if not resp:
            return None

        try:
            data = resp.json()
        except Exception:
            return None

        if data.get("status") != "success":
            return None

        tz_name = data.get("timezone", "")
        if not tz_name:
            return None

        utc_offset, local_time = self._compute_offset_and_time(tz_name)

        return ModuleResult(
            module_name=self.name,
            source="ip-api.com",
            finding_type="timezone_info",
            title=f"Timezone for IP {ip}",
            content=f"{tz_name} (UTC{utc_offset}), local time: {local_time}",
            data={
                "timezone": tz_name,
                "utc_offset": utc_offset,
                "current_local_time": local_time,
                "source": "ip-api.com",
            },
            confidence=70,
        )

    # ------------------------------------------------------------------
    # Domain → IP resolution
    # ------------------------------------------------------------------

    async def _resolve_domain_ip(self, domain: str) -> str | None:
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
    # Phone → timezone via phonenumbers
    # ------------------------------------------------------------------

    def _tz_from_phone(self, phone_str: str) -> ModuleResult | None:
        if not _HAS_PHONENUMBERS:
            self.logger.debug("phonenumbers not installed — skipping phone timezone")
            return None

        try:
            parsed = phonenumbers.parse(phone_str, "US")
            tzs = list(time_zones_for_number(parsed))
            if not tzs:
                return None

            # Use the first timezone
            tz_name = tzs[0]
            utc_offset, local_time = self._compute_offset_and_time(tz_name)

            return ModuleResult(
                module_name=self.name,
                source="phonenumbers",
                finding_type="timezone_info",
                title=f"Timezone for phone {phone_str}",
                content=f"{tz_name} (UTC{utc_offset}), local time: {local_time}",
                data={
                    "timezone": tz_name,
                    "utc_offset": utc_offset,
                    "current_local_time": local_time,
                    "source": "phonenumbers",
                    "all_timezones": tzs,
                },
                confidence=65,
            )
        except Exception as e:
            self.logger.debug(f"Phone timezone failed for {phone_str}: {e}")
            return None

    # ------------------------------------------------------------------
    # City/state → timezone via Nominatim + timezonefinder
    # ------------------------------------------------------------------

    async def _tz_from_city_state(
        self, city: str, state: str
    ) -> ModuleResult | None:
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

        tz_name = None

        # Try timezonefinder first
        if _HAS_TZFINDER:
            try:
                tf = TimezoneFinder()
                tz_name = tf.timezone_at(lat=lat, lng=lon)
            except Exception as e:
                self.logger.debug(f"TimezoneFinder failed: {e}")

        # Fallback: use ip-api with coordinates-based lookup is not available,
        # so we just report what we have
        if not tz_name:
            self.logger.debug(
                "timezonefinder not available — timezone from city/state not resolved"
            )
            return None

        utc_offset, local_time = self._compute_offset_and_time(tz_name)

        return ModuleResult(
            module_name=self.name,
            source="timezonefinder",
            finding_type="timezone_info",
            title=f"Timezone for {city}, {state}",
            content=f"{tz_name} (UTC{utc_offset}), local time: {local_time}",
            data={
                "timezone": tz_name,
                "utc_offset": utc_offset,
                "current_local_time": local_time,
                "source": "timezonefinder",
                "lat": lat,
                "lon": lon,
            },
            confidence=80,
        )

    # ------------------------------------------------------------------
    # UTC offset and local time computation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_offset_and_time(tz_name: str) -> tuple[str, str]:
        """Return (utc_offset_string, local_time_string) for a timezone name."""
        if not _HAS_ZONEINFO:
            return ("unknown", "unknown")

        try:
            tz = zoneinfo.ZoneInfo(tz_name)
            now = datetime.now(tz)
            offset = now.utcoffset()
            if offset is not None:
                total_seconds = int(offset.total_seconds())
                hours, remainder = divmod(abs(total_seconds), 3600)
                minutes = remainder // 60
                sign = "+" if total_seconds >= 0 else "-"
                offset_str = f"{sign}{hours:02d}:{minutes:02d}"
            else:
                offset_str = "unknown"
            local_time = now.strftime("%Y-%m-%d %H:%M:%S %Z")
            return offset_str, local_time
        except Exception:
            return ("unknown", "unknown")
