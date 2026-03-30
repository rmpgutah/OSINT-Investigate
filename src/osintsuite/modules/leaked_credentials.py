"""Leaked credentials module — checks for breach exposure and credential mentions."""

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


class LeakedCredentialsModule(BaseModule):
    name = "leaked_credentials"
    description = "Check for leaked credentials and breach exposure"

    HIBP_BREACHES_API = "https://haveibeenpwned.com/api/v3/breaches"

    def applicable_target_types(self) -> list[str]:
        return ["email", "domain"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        label = target.label
        if not label:
            self.logger.info("No label available on target, skipping leaked credentials")
            return results

        domain = label
        if target.target_type == "email" and "@" in label:
            domain = label.split("@")[1]

        # 1. HIBP public breach list (match by domain)
        results.extend(await self._check_hibp_breaches(domain))

        # 2. DDG search for credential mentions
        results.extend(await self._search_credential_mentions(label))

        # Summary finding
        results.append(
            ModuleResult(
                module_name=self.name,
                source="leaked_credentials",
                finding_type="leaked_creds_summary",
                title=f"Leaked credentials summary for {label}",
                content=(
                    f"Found {len(results)} breach/credential-related result(s) "
                    f'for "{label}".'
                ),
                data={
                    "target": label,
                    "total_results": len(results),
                },
                confidence=65,
            )
        )

        return results

    # ------------------------------------------------------------------
    # HIBP breach check
    # ------------------------------------------------------------------

    async def _check_hibp_breaches(self, domain: str) -> list[ModuleResult]:
        """Check HIBP public breach list for domain matches."""
        results: list[ModuleResult] = []

        try:
            response = await self.fetch(self.HIBP_BREACHES_API)
        except Exception as exc:
            self.logger.warning(f"HIBP breaches API request failed: {exc}")
            return results

        if not response or response.status_code != 200:
            return results

        try:
            breaches = response.json()
        except Exception:
            self.logger.warning("Failed to parse HIBP breaches JSON")
            return results

        if not isinstance(breaches, list):
            return results

        domain_lower = domain.lower()
        for breach in breaches:
            breach_domain = breach.get("Domain", "").lower()
            breach_name = breach.get("Name", "")
            if breach_domain == domain_lower:
                pwn_count = breach.get("PwnCount", 0)
                breach_date = breach.get("BreachDate", "unknown")
                data_classes = breach.get("DataClasses", [])

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="hibp",
                        finding_type="breach_exposure",
                        title=f"Breach: {breach_name} ({breach_date})",
                        content=(
                            f"Domain {domain} was breached in '{breach_name}' on {breach_date}. "
                            f"Affected accounts: {pwn_count:,}. "
                            f"Exposed data: {', '.join(data_classes[:5])}"
                        ),
                        data={
                            "breach_name": breach_name,
                            "domain": breach_domain,
                            "breach_date": breach_date,
                            "pwn_count": pwn_count,
                            "data_classes": data_classes,
                            "source": "hibp",
                        },
                        confidence=80,
                    )
                )

        self.logger.info(f"HIBP found {len(results)} breach(es) for domain '{domain}'")
        return results

    # ------------------------------------------------------------------
    # DDG credential mention search
    # ------------------------------------------------------------------

    async def _search_credential_mentions(self, target: str) -> list[ModuleResult]:
        """Search DDG for credential leak mentions."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping credential mention search"
            )
            return []

        results: list[ModuleResult] = []
        queries = [
            f'"{target}" leaked OR credentials OR combolist OR dump',
            f'"{target}" breach OR "data leak"',
        ]

        for query in queries:
            try:
                hits: list[dict[str, Any]] = await asyncio.to_thread(
                    self._sync_search, query
                )
            except Exception as exc:
                self.logger.warning(f"DDG credential search failed for '{query}': {exc}")
                continue

            for hit in hits[:5]:
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="credential_mention",
                        title=hit.get("title", f"Credential mention for {target}"),
                        content=hit.get("body", None),
                        data={
                            "url": hit.get("href", ""),
                            "snippet": hit.get("body", ""),
                            "source": "duckduckgo_dork",
                        },
                        confidence=60,
                    )
                )

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
