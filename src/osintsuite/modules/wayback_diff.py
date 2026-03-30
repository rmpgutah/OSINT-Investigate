"""Wayback diff module — compares historical snapshots of a domain via the Wayback Machine."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class WaybackDiffModule(BaseModule):
    name = "wayback_diff"
    description = "Wayback Machine snapshot comparison and historical change detection"

    CDX_API = "https://web.archive.org/cdx/search/cdx"

    def applicable_target_types(self) -> list[str]:
        return ["domain"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        domain = target.label
        if not domain:
            self.logger.info("No domain available on target, skipping Wayback diff")
            return results

        # 1. Get CDX snapshot list
        snapshots = await self._get_snapshots(domain)

        if not snapshots:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="wayback",
                    finding_type="wayback_summary",
                    title=f"No Wayback Machine snapshots found for {domain}",
                    content=f"The Wayback Machine CDX API returned no snapshots for {domain}.",
                    data={"domain": domain, "total_snapshots": 0},
                    confidence=60,
                )
            )
            return results

        # 2. Report snapshot metadata
        oldest = snapshots[0]
        newest = snapshots[-1]
        total = len(snapshots)

        results.append(
            ModuleResult(
                module_name=self.name,
                source="wayback_cdx",
                finding_type="wayback_snapshot",
                title=f"Oldest snapshot: {oldest.get('timestamp', 'unknown')}",
                content=(
                    f"Oldest Wayback snapshot for {domain} dated "
                    f"{self._format_timestamp(oldest.get('timestamp', ''))}. "
                    f"Status: {oldest.get('statuscode', 'unknown')}"
                ),
                data={
                    "domain": domain,
                    "timestamp": oldest.get("timestamp", ""),
                    "statuscode": oldest.get("statuscode", ""),
                    "url": f"https://web.archive.org/web/{oldest.get('timestamp', '')}/{domain}",
                    "type": "oldest",
                },
                confidence=70,
            )
        )

        results.append(
            ModuleResult(
                module_name=self.name,
                source="wayback_cdx",
                finding_type="wayback_snapshot",
                title=f"Newest snapshot: {newest.get('timestamp', 'unknown')}",
                content=(
                    f"Newest Wayback snapshot for {domain} dated "
                    f"{self._format_timestamp(newest.get('timestamp', ''))}. "
                    f"Status: {newest.get('statuscode', 'unknown')}"
                ),
                data={
                    "domain": domain,
                    "timestamp": newest.get("timestamp", ""),
                    "statuscode": newest.get("statuscode", ""),
                    "url": f"https://web.archive.org/web/{newest.get('timestamp', '')}/{domain}",
                    "type": "newest",
                },
                confidence=70,
            )
        )

        # 3. Compare oldest vs newest content
        change_result = await self._compare_snapshots(domain, oldest, newest)
        if change_result:
            results.append(change_result)

        # Summary finding
        results.append(
            ModuleResult(
                module_name=self.name,
                source="wayback",
                finding_type="wayback_summary",
                title=f"Wayback Machine summary for {domain}",
                content=(
                    f"Found {total} total snapshot(s) for {domain}. "
                    f"Date range: {self._format_timestamp(oldest.get('timestamp', ''))} "
                    f"to {self._format_timestamp(newest.get('timestamp', ''))}."
                ),
                data={
                    "domain": domain,
                    "total_snapshots": total,
                    "oldest_timestamp": oldest.get("timestamp", ""),
                    "newest_timestamp": newest.get("timestamp", ""),
                },
                confidence=60,
            )
        )

        return results

    # ------------------------------------------------------------------
    # CDX API
    # ------------------------------------------------------------------

    async def _get_snapshots(self, domain: str) -> list[dict[str, str]]:
        """Query the Wayback CDX API for snapshot list."""
        url = (
            f"{self.CDX_API}?url={domain}&output=json&fl=timestamp,statuscode,digest"
            f"&collapse=digest&limit=500"
        )

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"Wayback CDX API request failed: {exc}")
            return []

        if not response or response.status_code != 200:
            return []

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse Wayback CDX JSON response")
            return []

        if not isinstance(data, list) or len(data) < 2:
            return []

        # First row is header
        headers = data[0]
        snapshots: list[dict[str, str]] = []
        for row in data[1:]:
            entry = dict(zip(headers, row))
            snapshots.append(entry)

        return snapshots

    # ------------------------------------------------------------------
    # Snapshot comparison
    # ------------------------------------------------------------------

    async def _compare_snapshots(
        self, domain: str, oldest: dict, newest: dict
    ) -> ModuleResult | None:
        """Fetch oldest and newest snapshot content and compare word counts."""
        oldest_ts = oldest.get("timestamp", "")
        newest_ts = newest.get("timestamp", "")

        if oldest_ts == newest_ts:
            return None

        oldest_url = f"https://web.archive.org/web/{oldest_ts}id_/{domain}"
        newest_url = f"https://web.archive.org/web/{newest_ts}id_/{domain}"

        oldest_text = ""
        newest_text = ""

        try:
            resp = await self.fetch(oldest_url)
            if resp and resp.status_code == 200:
                oldest_text = resp.text[:10000]
        except Exception:
            pass

        try:
            resp = await self.fetch(newest_url)
            if resp and resp.status_code == 200:
                newest_text = resp.text[:10000]
        except Exception:
            pass

        if not oldest_text and not newest_text:
            return None

        oldest_words = set(oldest_text.split())
        newest_words = set(newest_text.split())
        added = len(newest_words - oldest_words)
        removed = len(oldest_words - newest_words)

        return ModuleResult(
            module_name=self.name,
            source="wayback_diff",
            finding_type="wayback_change",
            title=f"Content changes detected for {domain}",
            content=(
                f"Comparing oldest ({self._format_timestamp(oldest_ts)}) vs "
                f"newest ({self._format_timestamp(newest_ts)}) snapshot: "
                f"~{added} words added, ~{removed} words removed."
            ),
            data={
                "domain": domain,
                "oldest_timestamp": oldest_ts,
                "newest_timestamp": newest_ts,
                "words_added": added,
                "words_removed": removed,
            },
            confidence=65,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_timestamp(ts: str) -> str:
        """Format a Wayback timestamp (YYYYMMDDHHmmss) to readable date."""
        if len(ts) >= 8:
            return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
        return ts
