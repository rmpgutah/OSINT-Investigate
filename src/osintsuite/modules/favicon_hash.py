"""Favicon hash module — fetch favicon, compute hash, search for related infra."""

from __future__ import annotations

import asyncio
import hashlib
import struct
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    from duckduckgo_search import DDGS

    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False


class FaviconHashModule(BaseModule):
    name = "favicon_hash"
    description = "Favicon hash analysis — compute MD5/MurmurHash and find related infrastructure"

    def applicable_target_types(self) -> list[str]:
        return ["domain"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        domain = target.domain or target.label
        if not domain:
            self.logger.info("No domain available on target, skipping favicon hash")
            return results

        domain = domain.split("//")[-1].split("/")[0].strip().lower()

        # 1. Fetch favicon
        favicon_data = await self._fetch_favicon(domain)
        if not favicon_data:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="favicon_hash",
                    finding_type="favicon_not_found",
                    title=f"No favicon found for {domain}",
                    content=f"Could not retrieve favicon.ico from {domain}.",
                    data={"domain": domain, "favicon_found": False},
                    confidence=30,
                )
            )
            return results

        # 2. Compute hashes
        md5_hash = hashlib.md5(favicon_data).hexdigest()  # noqa: S324
        mmh3_hash = self._mmh3_hash(favicon_data)

        results.append(
            ModuleResult(
                module_name=self.name,
                source="favicon",
                finding_type="favicon_hash",
                title=f"Favicon hash for {domain}",
                content=(
                    f"Favicon MD5: {md5_hash}, MurmurHash3: {mmh3_hash}. "
                    f"Size: {len(favicon_data)} bytes."
                ),
                data={
                    "domain": domain,
                    "md5": md5_hash,
                    "mmh3": mmh3_hash,
                    "size_bytes": len(favicon_data),
                    "favicon_found": True,
                },
                confidence=70,
            )
        )

        # 3. DDG search for matching favicon hashes (Shodan dork)
        if _HAS_DDGS:
            related = await self._search_related(domain, mmh3_hash, md5_hash)
            results.extend(related)

        # Summary
        results.append(
            ModuleResult(
                module_name=self.name,
                source="favicon_hash",
                finding_type="favicon_hash_summary",
                title=f"Favicon analysis for {domain}",
                content=(
                    f"Computed favicon hashes for {domain}. "
                    f"MD5: {md5_hash}, MurmurHash3: {mmh3_hash}. "
                    f"Found {len(results) - 1} related result(s)."
                ),
                data={
                    "domain": domain,
                    "md5": md5_hash,
                    "mmh3": mmh3_hash,
                    "total_results": len(results),
                },
                confidence=70,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Favicon fetch
    # ------------------------------------------------------------------

    async def _fetch_favicon(self, domain: str) -> bytes | None:
        """Try to fetch favicon from common paths."""
        paths = [
            f"https://{domain}/favicon.ico",
            f"https://{domain}/favicon.png",
            f"http://{domain}/favicon.ico",
        ]

        for url in paths:
            try:
                response = await self.fetch(url)
                if response and len(response.content) > 0:
                    return response.content
            except Exception:
                continue

        return None

    # ------------------------------------------------------------------
    # MurmurHash3 (simple 32-bit implementation)
    # ------------------------------------------------------------------

    @staticmethod
    def _mmh3_hash(data: bytes) -> int:
        """Simple MurmurHash3 32-bit implementation for favicon hashing."""
        import base64

        # Encode favicon as base64 (this is how Shodan computes it)
        b64_data = base64.b64encode(data)

        length = len(b64_data)
        c1 = 0xCC9E2D51
        c2 = 0x1B873593
        h1 = 0  # seed
        rounded_end = (length & 0xFFFFFFFC)

        for i in range(0, rounded_end, 4):
            k1 = (
                (b64_data[i] & 0xFF)
                | ((b64_data[i + 1] & 0xFF) << 8)
                | ((b64_data[i + 2] & 0xFF) << 16)
                | ((b64_data[i + 3] & 0xFF) << 24)
            )
            k1 = (k1 * c1) & 0xFFFFFFFF
            k1 = ((k1 << 15) | (k1 >> 17)) & 0xFFFFFFFF
            k1 = (k1 * c2) & 0xFFFFFFFF
            h1 ^= k1
            h1 = ((h1 << 13) | (h1 >> 19)) & 0xFFFFFFFF
            h1 = ((h1 * 5) + 0xE6546B64) & 0xFFFFFFFF

        k1 = 0
        remaining = length & 3
        if remaining >= 3:
            k1 ^= (b64_data[rounded_end + 2] & 0xFF) << 16
        if remaining >= 2:
            k1 ^= (b64_data[rounded_end + 1] & 0xFF) << 8
        if remaining >= 1:
            k1 ^= b64_data[rounded_end] & 0xFF
            k1 = (k1 * c1) & 0xFFFFFFFF
            k1 = ((k1 << 15) | (k1 >> 17)) & 0xFFFFFFFF
            k1 = (k1 * c2) & 0xFFFFFFFF
            h1 ^= k1

        h1 ^= length
        h1 ^= h1 >> 16
        h1 = (h1 * 0x85EBCA6B) & 0xFFFFFFFF
        h1 ^= h1 >> 13
        h1 = (h1 * 0xC2B2AE35) & 0xFFFFFFFF
        h1 ^= h1 >> 16

        # Convert to signed 32-bit
        if h1 >= 0x80000000:
            h1 -= 0x100000000

        return h1

    # ------------------------------------------------------------------
    # DDG search for related infrastructure
    # ------------------------------------------------------------------

    async def _search_related(
        self, domain: str, mmh3: int, md5: str
    ) -> list[ModuleResult]:
        if not _HAS_DDGS:
            return []

        results: list[ModuleResult] = []
        queries = [
            f'shodan favicon hash {mmh3}',
            f'"{md5}" favicon hash',
        ]

        for query in queries:
            try:
                hits: list[dict[str, Any]] = await asyncio.to_thread(
                    self._sync_search, query
                )
            except Exception as exc:
                self.logger.warning(f"DDG favicon search failed: {exc}")
                continue

            for hit in hits[:3]:
                title = hit.get("title", "")
                url = hit.get("href", "")
                body = hit.get("body", "")

                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="duckduckgo",
                        finding_type="favicon_related_infra",
                        title=f"Related infrastructure: {title[:80]}",
                        content=body[:200] if body else None,
                        data={
                            "title": title,
                            "url": url,
                            "snippet": body[:300],
                            "original_domain": domain,
                            "mmh3_hash": mmh3,
                        },
                        confidence=60,
                    )
                )

        return results

    @staticmethod
    def _sync_search(query: str) -> list[dict[str, Any]]:
        """Synchronous DuckDuckGo search (called in a thread)."""
        return list(DDGS().text(query, max_results=5))
