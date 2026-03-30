"""Professional and occupational license verification via search engine dorking."""

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


class ProfessionalLicenseModule(BaseModule):
    name = "professional_license"
    description = "Professional and occupational license verification"

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search is not installed — skipping professional_license module"
            )
            return [
                ModuleResult(
                    module_name=self.name,
                    source="duckduckgo",
                    finding_type="license_summary",
                    title="Professional License module unavailable",
                    content="Install duckduckgo_search to enable this module.",
                    data={"error": "duckduckgo_search not installed"},
                    confidence=0,
                )
            ]

        full_name = target.full_name or target.label
        state = target.state or ""

        dorks = self._generate_dorks(full_name, state)
        results: list[ModuleResult] = []
        seen_urls: set[str] = set()
        total_found = 0

        for idx, query in enumerate(dorks):
            if idx > 0:
                await asyncio.sleep(3)

            hits = await self._search(query)
            for hit in hits:
                url = hit.get("href", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                if total_found >= 10:
                    break

                title = hit.get("title", "")
                snippet = hit.get("body", "")
                license_type = self._guess_license_type(title, snippet)

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="license_record",
                        title=f"License result: {title[:120]}",
                        content=snippet[:500] if snippet else None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "license_type_guess": license_type,
                        },
                        confidence=45,
                    )
                )
                total_found += 1

            if total_found >= 10:
                break

        # Summary finding
        results.append(
            ModuleResult(
                module_name=self.name,
                source="duckduckgo",
                finding_type="license_summary",
                title=f"Professional license search for {full_name} ({total_found} results)",
                content=None,
                data={
                    "full_name": full_name,
                    "state": state,
                    "total_results": total_found,
                    "dorks_run": len(dorks),
                },
                confidence=50,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Dork generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_dorks(full_name: str, state: str) -> list[str]:
        dorks = [
            f'"{full_name}" "professional license" OR "medical license" OR "law license" OR "real estate license"',
            f'"{full_name}" site:*.state.*.us license',
        ]
        if state:
            dorks.insert(0, f'"{full_name}" license {state}')
        return dorks

    # ------------------------------------------------------------------
    # License type heuristic
    # ------------------------------------------------------------------

    @staticmethod
    def _guess_license_type(title: str, snippet: str) -> str:
        text = (title + " " + snippet).lower()
        mapping = {
            "medical": ["medical license", "physician", "doctor", "md license", "nursing"],
            "law": ["bar admission", "attorney", "law license", "esquire", "bar number"],
            "real_estate": ["real estate", "realtor", "broker license", "real estate license"],
            "engineering": ["professional engineer", "pe license", "engineering license"],
            "accounting": ["cpa", "certified public accountant", "accounting license"],
            "teaching": ["teaching certificate", "educator license", "teaching license"],
            "pharmacy": ["pharmacist", "pharmacy license", "rpn"],
            "dental": ["dentist", "dental license", "dds"],
            "insurance": ["insurance license", "insurance agent", "insurance broker"],
            "contractor": ["contractor license", "general contractor", "building license"],
        }
        for license_type, keywords in mapping.items():
            if any(kw in text for kw in keywords):
                return license_type
        return "unknown"

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
