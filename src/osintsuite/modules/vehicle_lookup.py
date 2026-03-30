"""Vehicle identification, VIN decoding, and recall lookups via NHTSA APIs."""

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

_NHTSA_DECODE_URL = "https://vpic.nhtsa.dot.gov/api/vehicles/decodevin/{vin}?format=json"
_NHTSA_RECALLS_URL = (
    "https://api.nhtsa.gov/recalls/recallsByVehicle"
    "?make={make}&model={model}&modelYear={year}"
)


class VehicleLookupModule(BaseModule):
    name = "vehicle_lookup"
    description = "Vehicle identification, VIN decoding, and recall lookups via NHTSA"

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        metadata = target.metadata_ or {}

        vin = metadata.get("vin") or metadata.get("VIN")
        license_plate = metadata.get("license_plate") or metadata.get("LICENSE_PLATE")

        if vin:
            decode_result = await self._decode_vin(vin)
            if decode_result:
                results.append(decode_result)

                # If decoding succeeded, try recall lookup
                make = decode_result.data.get("make", "")
                model = decode_result.data.get("model", "")
                year = decode_result.data.get("year", "")
                if make and model and year:
                    recall_results = await self._check_recalls(make, model, year)
                    results.extend(recall_results)
        else:
            # No VIN available — fall back to DDG dork search
            name = target.full_name or target.label
            state = target.state or ""
            if name:
                dork_results = await self._dork_vehicle_search(name, state)
                results.extend(dork_results)

            if not results:
                self.logger.info(
                    "No VIN in target metadata and no name for dork search"
                )

        return results

    # ------------------------------------------------------------------
    # VIN Decoding via NHTSA
    # ------------------------------------------------------------------

    async def _decode_vin(self, vin: str) -> ModuleResult | None:
        """Decode a VIN using the free NHTSA vPIC API."""
        url = _NHTSA_DECODE_URL.format(vin=vin)
        response = await self.fetch(url)
        if not response:
            self.logger.warning(f"NHTSA VIN decode request failed for {vin}")
            return None

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse NHTSA VIN decode response")
            return None

        decode_results = data.get("Results", [])
        if not decode_results:
            return None

        # Build a lookup dict from the NHTSA results list
        fields: dict[str, str] = {}
        for item in decode_results:
            var = item.get("Variable", "")
            val = item.get("Value")
            if var and val and str(val).strip():
                fields[var] = str(val).strip()

        make = fields.get("Make", "")
        model = fields.get("Model", "")
        year = fields.get("Model Year", "")
        body_class = fields.get("Body Class", "")
        engine = fields.get("Engine Model", "") or fields.get(
            "Engine Number of Cylinders", ""
        )
        fuel_type = fields.get("Fuel Type - Primary", "")
        plant_city = fields.get("Plant City", "")

        title_parts = [p for p in [year, make, model] if p]
        title = " ".join(title_parts) if title_parts else f"VIN {vin}"

        return ModuleResult(
            module_name=self.name,
            source="nhtsa",
            finding_type="vin_decode",
            title=f"VIN Decode: {title}",
            content=f"Decoded VIN {vin}: {title}",
            data={
                "vin": vin,
                "make": make,
                "model": model,
                "year": year,
                "body_class": body_class,
                "engine": engine,
                "fuel_type": fuel_type,
                "plant_city": plant_city,
            },
            confidence=85,
        )

    # ------------------------------------------------------------------
    # Recall Lookup via NHTSA
    # ------------------------------------------------------------------

    async def _check_recalls(
        self, make: str, model: str, year: str
    ) -> list[ModuleResult]:
        """Check NHTSA recalls for a given make/model/year."""
        url = _NHTSA_RECALLS_URL.format(make=make, model=model, year=year)
        response = await self.fetch(url)
        if not response:
            self.logger.warning(f"NHTSA recall request failed for {make} {model}")
            return []

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse NHTSA recall response")
            return []

        recall_list = data.get("results", [])
        results: list[ModuleResult] = []

        for recall in recall_list:
            recall_number = recall.get("NHTSACampaignNumber", "")
            component = recall.get("Component", "")
            summary = recall.get("Summary", "")
            consequence = recall.get("Consequence", "")

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="nhtsa",
                    finding_type="vehicle_recall",
                    title=f"Recall {recall_number}: {component}",
                    content=summary,
                    data={
                        "recall_number": recall_number,
                        "component": component,
                        "summary": summary,
                        "consequence": consequence,
                    },
                    confidence=90,
                )
            )

        self.logger.info(
            f"Found {len(results)} recalls for {year} {make} {model}"
        )
        return results

    # ------------------------------------------------------------------
    # DDG Dork Fallback
    # ------------------------------------------------------------------

    async def _dork_vehicle_search(
        self, full_name: str, state: str
    ) -> list[ModuleResult]:
        """Fall back to DuckDuckGo dorking when no VIN is available."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping vehicle dork search"
            )
            return []

        query = f'"{full_name}" vehicle registration'
        if state:
            query += f" {state}"

        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
        except Exception as exc:
            self.logger.warning(f"Vehicle dork search failed: {exc}")
            return []

        results: list[ModuleResult] = []
        for hit in hits[:10]:
            title = hit.get("title", "")
            url = hit.get("href", "")
            snippet = hit.get("body", "")
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="vehicle_search",
                    title=title or "Vehicle search result",
                    content=snippet,
                    data={
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "query": query,
                    },
                    confidence=30,
                )
            )

        self.logger.info(
            f"Vehicle dork search found {len(results)} results for '{full_name}'"
        )
        return results

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
