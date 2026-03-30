"""Phone deep analysis module — validates, classifies, and searches for phone number intelligence."""

from __future__ import annotations

import asyncio
import re
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

try:
    import phonenumbers
    from phonenumbers import carrier as pn_carrier
    from phonenumbers import geocoder as pn_geocoder
    from phonenumbers import number_type as pn_number_type_func
    from phonenumbers import PhoneNumberType

    _HAS_PHONENUMBERS = True
except ImportError:
    _HAS_PHONENUMBERS = False


class PhoneDeepModule(BaseModule):
    name = "phone_deep"
    description = "Deep phone number analysis — validation, carrier, region, and web mentions"

    NUMVERIFY_API = "https://apilayer.net/api/validate"
    MAX_RESULTS = 15

    # Map phonenumbers PhoneNumberType to human labels
    _NUMBER_TYPE_MAP: dict[int, str] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if _HAS_PHONENUMBERS:
            self._NUMBER_TYPE_MAP = {
                PhoneNumberType.MOBILE: "mobile",
                PhoneNumberType.FIXED_LINE: "landline",
                PhoneNumberType.FIXED_LINE_OR_MOBILE: "landline_or_mobile",
                PhoneNumberType.TOLL_FREE: "toll_free",
                PhoneNumberType.PREMIUM_RATE: "premium_rate",
                PhoneNumberType.SHARED_COST: "shared_cost",
                PhoneNumberType.VOIP: "voip",
                PhoneNumberType.PERSONAL_NUMBER: "personal",
                PhoneNumberType.PAGER: "pager",
                PhoneNumberType.UAN: "uan",
                PhoneNumberType.UNKNOWN: "unknown",
            }

    def applicable_target_types(self) -> list[str]:
        return ["phone"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        phone_number = getattr(target, "phone", "") or target.label or ""
        if not phone_number:
            self.logger.info("No phone number available on target, skipping phone_deep")
            return []

        # Normalize the phone number string
        phone_number = phone_number.strip()

        # Phase 1: phonenumbers library analysis
        analysis = self._analyze_phone(phone_number)
        if analysis:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="phonenumbers_lib",
                    finding_type="phone_analysis",
                    title=f"Phone analysis: {phone_number}",
                    content=(
                        f"Carrier: {analysis['carrier']} | Region: {analysis['region']} | "
                        f"Type: {analysis['number_type']} | Valid: {analysis['is_valid']}"
                    ),
                    data={
                        "title": f"Phone analysis: {phone_number}",
                        "url": "",
                        "snippet": f"Carrier: {analysis['carrier']}, Region: {analysis['region']}",
                        "source": "phonenumbers_lib",
                        "carrier": analysis["carrier"],
                        "region": analysis["region"],
                        "number_type": analysis["number_type"],
                        "is_valid": analysis["is_valid"],
                    },
                    confidence=75,
                )
            )

        # Phase 2: NumVerify API (free tier, limited)
        numverify_result = await self._check_numverify(phone_number)
        if numverify_result:
            results.append(numverify_result)

        # Phase 3: DuckDuckGo dork searches
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search is not installed — skipping DDG dorks for phone_deep"
            )
        else:
            dorks = self._generate_dorks(phone_number)
            seen_urls: set[str] = set()
            total_found = len(results)

            for idx, (query, finding_type, confidence) in enumerate(dorks):
                if idx > 0:
                    await asyncio.sleep(3)

                hits = await self._search(query)
                for hit in hits:
                    url = hit.get("href", "")
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    if total_found >= self.MAX_RESULTS:
                        break

                    title = hit.get("title", "")
                    snippet = hit.get("body", "")

                    results.append(
                        ModuleResult(
                            module_name=self.name,
                            source="duckduckgo",
                            finding_type=finding_type,
                            title=f"Phone mention: {title[:120]}",
                            content=snippet[:500] if snippet else None,
                            data={
                                "title": title,
                                "url": url,
                                "snippet": snippet,
                                "source": "duckduckgo",
                                "carrier": analysis.get("carrier", "") if analysis else "",
                                "region": analysis.get("region", "") if analysis else "",
                                "number_type": analysis.get("number_type", "") if analysis else "",
                                "is_valid": analysis.get("is_valid", "") if analysis else "",
                            },
                            confidence=confidence,
                        )
                    )
                    total_found += 1

                if total_found >= self.MAX_RESULTS:
                    break

        # Summary
        results.append(
            ModuleResult(
                module_name=self.name,
                source="phone_deep",
                finding_type="phone_deep_summary",
                title=f"Phone deep analysis for {phone_number} ({len(results)} results)",
                content=None,
                data={
                    "phone_number": phone_number,
                    "total_results": len(results),
                    "has_phonenumbers_lib": _HAS_PHONENUMBERS,
                    "has_ddgs": _HAS_DDGS,
                },
                confidence=50,
            )
        )

        return results

    # ------------------------------------------------------------------
    # phonenumbers library analysis
    # ------------------------------------------------------------------

    def _analyze_phone(self, phone_number: str) -> dict[str, Any] | None:
        """Use the phonenumbers library to validate and classify."""
        if not _HAS_PHONENUMBERS:
            self.logger.debug("phonenumbers library not installed")
            return None

        try:
            parsed = phonenumbers.parse(phone_number, "US")
            is_valid = phonenumbers.is_valid_number(parsed)
            carrier_name = pn_carrier.name_for_number(parsed, "en") or ""
            region = pn_geocoder.description_for_number(parsed, "en") or ""
            num_type_int = pn_number_type_func(parsed)
            num_type_str = self._NUMBER_TYPE_MAP.get(num_type_int, "unknown")

            return {
                "carrier": carrier_name,
                "region": region,
                "number_type": num_type_str,
                "is_valid": str(is_valid),
            }
        except Exception as exc:
            self.logger.warning(f"phonenumbers parse failed: {exc}")
            return None

    # ------------------------------------------------------------------
    # NumVerify API
    # ------------------------------------------------------------------

    async def _check_numverify(self, phone_number: str) -> ModuleResult | None:
        """Query NumVerify free tier (requires API key in env, skip if missing)."""
        import os

        api_key = os.environ.get("NUMVERIFY_API_KEY", "")
        if not api_key:
            self.logger.debug("NUMVERIFY_API_KEY not set, skipping NumVerify")
            return None

        clean_number = re.sub(r"[^\d+]", "", phone_number)
        params = {"access_key": api_key, "number": clean_number}
        url = f"{self.NUMVERIFY_API}?{urllib.parse.urlencode(params)}"

        response = await self.fetch(url)
        if not response:
            return None

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse NumVerify JSON response")
            return None

        if not data.get("valid", False) and not data.get("number"):
            return None

        return ModuleResult(
            module_name=self.name,
            source="numverify_api",
            finding_type="phone_analysis",
            title=f"NumVerify: {phone_number}",
            content=(
                f"Valid: {data.get('valid')} | "
                f"Carrier: {data.get('carrier', '')} | "
                f"Type: {data.get('line_type', '')} | "
                f"Location: {data.get('location', '')}"
            ),
            data={
                "title": f"NumVerify: {phone_number}",
                "url": "",
                "snippet": f"Carrier: {data.get('carrier', '')}",
                "source": "numverify_api",
                "carrier": data.get("carrier", ""),
                "region": data.get("location", ""),
                "number_type": data.get("line_type", ""),
                "is_valid": str(data.get("valid", False)),
            },
            confidence=70,
        )

    # ------------------------------------------------------------------
    # Dork generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_dorks(phone_number: str) -> list[tuple[str, str, int]]:
        """Return list of (query, finding_type, confidence) tuples."""
        return [
            (f'"{phone_number}" owner OR registered', "phone_owner_mention", 50),
            (f'"{phone_number}" complaint OR spam OR scam', "phone_complaint", 55),
            (f'site:whitepages.com "{phone_number}"', "phone_owner_mention", 50),
        ]

    # ------------------------------------------------------------------
    # Search helper
    # ------------------------------------------------------------------

    async def _search(self, query: str) -> list[dict[str, Any]]:
        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
            return hits
        except Exception as exc:
            self.logger.warning(f"Search failed for dork '{query}': {exc}")
            return []

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        return list(DDGS().text(query, max_results=10))
