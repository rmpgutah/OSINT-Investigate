"""Cell tower location lookup via OpenCelliD."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    import phonenumbers
    from phonenumbers import carrier, geocoder

    _HAS_PHONENUMBERS = True
except ImportError:
    _HAS_PHONENUMBERS = False

try:
    from duckduckgo_search import DDGS

    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False


class CellTowerModule(BaseModule):
    name = "cell_tower"
    description = "Cell tower location lookup via OpenCelliD"

    def applicable_target_types(self) -> list[str]:
        return ["phone"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        meta = target.metadata_ or {}

        # 1. Try precise cell tower lookup if MCC/MNC/LAC/CellID available
        mcc = meta.get("mcc")
        mnc = meta.get("mnc")
        lac = meta.get("lac")
        cellid = meta.get("cellid") or meta.get("cell_id")

        if mcc and mnc and lac and cellid:
            tower_result = await self._opencellid_lookup(mcc, mnc, lac, cellid)
            if tower_result:
                results.append(tower_result)

        # 2. Carrier info from phonenumbers library
        phone = target.phone or (target.label if target.target_type == "phone" else None)
        if phone and _HAS_PHONENUMBERS:
            carrier_result = self._get_carrier_info(phone)
            if carrier_result:
                results.append(carrier_result)

                # 3. Search for cell towers in target city via DDG
                carrier_name = carrier_result.data.get("carrier", "")
                city = target.city or carrier_result.data.get("location", "")
                if city:
                    tower_search = await self._ddg_tower_search(carrier_name, city)
                    results.extend(tower_search)

        if not results:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="cell_tower",
                    finding_type="cell_tower_summary",
                    title=f"No cell tower data available for {target.label}",
                    content=(
                        "No MCC/MNC/LAC/CellID in metadata and phone number "
                        "could not be parsed for carrier info."
                    ),
                    data={"phone": target.label},
                    confidence=10,
                )
            )

        return results

    async def _opencellid_lookup(
        self, mcc: int | str, mnc: int | str, lac: int | str, cellid: int | str
    ) -> ModuleResult | None:
        """Query OpenCelliD for cell tower location."""
        url = "https://opencellid.org/cell/get"
        params = {
            "key": "pk.demo",
            "mcc": str(mcc),
            "mnc": str(mnc),
            "lac": str(lac),
            "cellid": str(cellid),
            "format": "json",
        }

        try:
            resp = await self.fetch(url, params=params)
            if resp is None:
                return None

            data = resp.json()
            if data.get("status") == "ok" or data.get("lat"):
                return ModuleResult(
                    module_name=self.name,
                    source="opencellid",
                    finding_type="cell_tower",
                    title=f"Cell Tower: MCC={mcc} MNC={mnc} LAC={lac} CellID={cellid}",
                    content=(
                        f"Location: ({data.get('lat')}, {data.get('lon')}), "
                        f"Range: {data.get('range', 'N/A')}m"
                    ),
                    data={
                        "lat": data.get("lat"),
                        "lon": data.get("lon"),
                        "range": data.get("range"),
                        "mcc": int(mcc),
                        "mnc": int(mnc),
                        "lac": int(lac),
                        "cellid": int(cellid),
                    },
                    confidence=75,
                )
        except Exception as e:
            self.logger.warning(f"OpenCelliD lookup failed: {e}")

        return None

    def _get_carrier_info(self, phone: str) -> ModuleResult | None:
        """Extract carrier and location info from phonenumbers library."""
        try:
            parsed = phonenumbers.parse(phone, "US")
        except phonenumbers.NumberParseException:
            return None

        if not phonenumbers.is_valid_number(parsed):
            return None

        carrier_name = carrier.name_for_number(parsed, "en") or "Unknown"
        location = geocoder.description_for_number(parsed, "en") or "Unknown"

        return ModuleResult(
            module_name=self.name,
            source="phonenumbers",
            finding_type="carrier_info",
            title=f"Carrier: {carrier_name} — {location}",
            content=f"Phone: {phone}, Carrier: {carrier_name}, Location: {location}",
            data={
                "phone": phone,
                "carrier": carrier_name,
                "location": location,
                "country_code": parsed.country_code,
            },
            confidence=80,
        )

    async def _ddg_tower_search(
        self, carrier_name: str, city: str
    ) -> list[ModuleResult]:
        """Search for cell tower info near a city via DDG."""
        if not _HAS_DDGS:
            return []

        results: list[ModuleResult] = []
        query = f'"{carrier_name}" cell tower {city}'

        try:
            hits = await asyncio.to_thread(
                lambda: list(DDGS().text(query, max_results=5))
            )
            for h in hits:
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="cell_tower_mention",
                        title=h.get("title", ""),
                        content=h.get("body", ""),
                        data={
                            "url": h.get("href", ""),
                            "title": h.get("title", ""),
                            "snippet": h.get("body", ""),
                            "carrier": carrier_name,
                            "city": city,
                        },
                        confidence=30,
                    )
                )
        except Exception as e:
            self.logger.warning(f"DDG cell tower search failed: {e}")

        return results
