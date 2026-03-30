"""Cloud exposure module — checks for exposed cloud storage buckets."""

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


class CloudExposureModule(BaseModule):
    name = "cloud_exposure"
    description = "Check for exposed cloud storage buckets (S3, Azure Blob, GCS)"

    CLOUD_PROBES = {
        "s3_bucket": "https://{name}.s3.amazonaws.com",
        "s3_path": "https://s3.amazonaws.com/{name}",
        "azure_blob": "https://{name}.blob.core.windows.net",
        "gcs_bucket": "https://storage.googleapis.com/{name}",
    }

    def applicable_target_types(self) -> list[str]:
        return ["domain", "organization"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        name = target.label
        if not name:
            self.logger.info("No label available on target, skipping cloud exposure")
            return results

        # Derive bucket name candidates from domain/org name
        candidates = self._derive_bucket_names(name)

        # 1. Probe cloud storage endpoints
        for candidate in candidates:
            results.extend(await self._probe_buckets(candidate))

        # 2. DDG search for grayhatwarfare mentions
        results.extend(await self._search_grayhatwarfare(name))

        # Summary finding
        bucket_count = sum(1 for r in results if r.finding_type == "cloud_bucket")
        results.append(
            ModuleResult(
                module_name=self.name,
                source="cloud_exposure",
                finding_type="cloud_summary",
                title=f"Cloud exposure summary for {name}",
                content=(
                    f"Found {bucket_count} potential cloud bucket exposure(s) and "
                    f"{len(results)} total result(s) for \"{name}\"."
                ),
                data={
                    "name": name,
                    "bucket_count": bucket_count,
                    "total_results": len(results),
                },
                confidence=70,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Cloud bucket probing
    # ------------------------------------------------------------------

    async def _probe_buckets(self, name: str) -> list[ModuleResult]:
        """Probe standard cloud storage URLs for the given name."""
        results: list[ModuleResult] = []

        for provider, url_template in self.CLOUD_PROBES.items():
            url = url_template.format(name=name)
            try:
                response = await self.fetch(url)
                if response and response.status_code in (200, 301, 302, 403):
                    status = response.status_code
                    accessible = status == 200
                    results.append(
                        ModuleResult(
                            module_name=self.name,
                            source=provider,
                            finding_type="cloud_bucket",
                            title=f"Cloud bucket found: {url} (HTTP {status})",
                            content=(
                                f"Cloud storage endpoint responded with HTTP {status}. "
                                f"{'Publicly accessible!' if accessible else 'Exists but access restricted.'}"
                            ),
                            data={
                                "url": url,
                                "provider": provider,
                                "status_code": status,
                                "accessible": accessible,
                                "bucket_name": name,
                            },
                            confidence=85,
                        )
                    )
            except Exception as exc:
                self.logger.debug(f"Probe failed for {url}: {exc}")

        return results

    # ------------------------------------------------------------------
    # GrayHatWarfare DDG search
    # ------------------------------------------------------------------

    async def _search_grayhatwarfare(self, name: str) -> list[ModuleResult]:
        """Search DDG for grayhatwarfare mentions of exposed buckets."""
        if not _HAS_DDGS:
            self.logger.warning(
                "duckduckgo_search not installed — skipping grayhatwarfare search"
            )
            return []

        results: list[ModuleResult] = []
        query = f'"{name}" site:buckets.grayhatwarfare.com'

        try:
            hits: list[dict[str, Any]] = await asyncio.to_thread(
                self._sync_search, query
            )
            for hit in hits[:5]:
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="grayhatwarfare",
                        finding_type="cloud_exposure_mention",
                        title=hit.get("title", f"Cloud exposure mention for {name}"),
                        content=hit.get("body", None),
                        data={
                            "url": hit.get("href", ""),
                            "snippet": hit.get("body", ""),
                            "source": "grayhatwarfare",
                        },
                        confidence=60,
                    )
                )
        except Exception as exc:
            self.logger.warning(f"DDG grayhatwarfare search failed: {exc}")

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_bucket_names(name: str) -> list[str]:
        """Derive potential bucket names from domain or org name."""
        candidates = [name]
        # Strip common TLDs for domain targets
        for tld in (".com", ".org", ".net", ".io", ".co"):
            if name.endswith(tld):
                candidates.append(name[: -len(tld)])
                break
        # Add hyphenated and dot-stripped variants
        base = candidates[-1] if len(candidates) > 1 else name
        if "." in base:
            candidates.append(base.replace(".", "-"))
        return list(dict.fromkeys(candidates))  # dedupe, preserve order

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=10))
