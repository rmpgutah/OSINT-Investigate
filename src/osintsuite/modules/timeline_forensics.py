"""Timeline forensics module — Certificate Transparency, Wayback Machine, historical evidence."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class TimelineForensicsModule(BaseModule):
    name = "timeline_forensics"
    description = "Certificate Transparency logs, Wayback Machine history, and evidence timeline"

    def applicable_target_types(self) -> list[str]:
        return ["domain", "ip", "email"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        indicator = target.label

        if "://" in indicator:
            indicator = indicator.split("://")[1].split("/")[0]

        if target.target_type in ("domain", "ip"):
            results.extend(await self._cert_transparency(indicator))
            results.extend(await self._wayback_machine(indicator))

        if target.target_type == "email":
            # For email, check the domain part
            if "@" in indicator:
                domain = indicator.split("@")[1]
                results.extend(await self._cert_transparency(domain))
                results.extend(await self._wayback_machine(domain))

        # Build a combined timeline from all timestamped findings
        if results:
            timeline = self._build_timeline(results)
            if timeline:
                results.append(timeline)

        return results

    async def _cert_transparency(self, domain: str) -> list[ModuleResult]:
        """Query crt.sh for Certificate Transparency logs."""
        url = f"https://crt.sh/?q={domain}&output=json"
        resp = await self.fetch(url)
        if not resp:
            return []

        try:
            certs = resp.json()
        except Exception:
            return []

        if not isinstance(certs, list) or len(certs) == 0:
            return []

        # Deduplicate by serial number and sort by date
        seen_serials = set()
        unique_certs = []
        for cert in certs:
            serial = cert.get("serial_number", "")
            if serial not in seen_serials:
                seen_serials.add(serial)
                unique_certs.append(cert)

        # Sort by not_before date
        unique_certs.sort(
            key=lambda c: c.get("not_before", ""),
            reverse=True,
        )

        # Take latest 30 unique certs
        recent = unique_certs[:30]

        # Identify all unique names (subdomains discovered)
        all_names = set()
        for cert in certs:
            name_value = cert.get("name_value", "")
            for name in name_value.split("\n"):
                name = name.strip().lstrip("*.")
                if name and name != domain:
                    all_names.add(name)

        cert_entries = [
            {
                "issuer": c.get("issuer_name", ""),
                "common_name": c.get("common_name", ""),
                "name_value": c.get("name_value", ""),
                "not_before": c.get("not_before", ""),
                "not_after": c.get("not_after", ""),
                "serial_number": c.get("serial_number", ""),
            }
            for c in recent
        ]

        results = [
            ModuleResult(
                module_name=self.name,
                source="crt.sh",
                finding_type="cert_history",
                title=f"CT logs: {len(unique_certs)} certificates for {domain}",
                content=f"{len(unique_certs)} unique certificates found | {len(all_names)} related hostnames discovered",
                data={
                    "domain": domain,
                    "total_certs": len(unique_certs),
                    "recent_certs": cert_entries,
                    "discovered_hostnames": sorted(all_names)[:50],
                },
                confidence=90,
            )
        ]

        return results

    async def _wayback_machine(self, domain: str) -> list[ModuleResult]:
        """Query the Wayback Machine CDX API for historical snapshots."""
        url = f"https://web.archive.org/cdx/search/cdx?url={domain}&output=json&limit=50&fl=timestamp,statuscode,mimetype,digest&collapse=timestamp:6"
        resp = await self.fetch(url)
        if not resp:
            return []

        try:
            rows = resp.json()
        except Exception:
            return []

        if not isinstance(rows, list) or len(rows) <= 1:
            return []

        # First row is headers
        headers_row = rows[0]
        snapshots = []
        for row in rows[1:]:
            entry = dict(zip(headers_row, row))
            ts = entry.get("timestamp", "")
            if len(ts) >= 8:
                try:
                    dt = datetime.strptime(ts[:8], "%Y%m%d")
                    entry["date"] = dt.strftime("%Y-%m-%d")
                    entry["wayback_url"] = f"https://web.archive.org/web/{ts}/{domain}"
                except ValueError:
                    entry["date"] = ts
            snapshots.append(entry)

        if not snapshots:
            return []

        first_seen = snapshots[-1].get("date", "unknown")
        last_seen = snapshots[0].get("date", "unknown")

        return [
            ModuleResult(
                module_name=self.name,
                source="wayback_machine",
                finding_type="wayback_snapshot",
                title=f"Wayback: {len(snapshots)} snapshots of {domain}",
                content=f"First archived: {first_seen} | Last archived: {last_seen} | Total snapshots: {len(snapshots)}",
                data={
                    "domain": domain,
                    "total_snapshots": len(snapshots),
                    "first_seen": first_seen,
                    "last_seen": last_seen,
                    "snapshots": snapshots[:30],
                },
                confidence=85,
            )
        ]

    def _build_timeline(self, results: list[ModuleResult]) -> ModuleResult | None:
        """Compile all timestamped findings into a single chronological timeline."""
        events = []

        for r in results:
            if r.finding_type == "cert_history":
                for cert in r.data.get("recent_certs", [])[:10]:
                    events.append({
                        "date": cert.get("not_before", "")[:10],
                        "type": "certificate_issued",
                        "source": "crt.sh",
                        "detail": f"Cert issued for {cert.get('common_name', 'unknown')} by {cert.get('issuer', 'unknown')[:50]}",
                    })

            elif r.finding_type == "wayback_snapshot":
                for snap in r.data.get("snapshots", [])[:10]:
                    events.append({
                        "date": snap.get("date", ""),
                        "type": "web_snapshot",
                        "source": "wayback_machine",
                        "detail": f"Archived ({snap.get('statuscode', '?')}) — {snap.get('wayback_url', '')}",
                    })

        if not events:
            return None

        # Sort chronologically
        events.sort(key=lambda e: e.get("date", ""))

        return ModuleResult(
            module_name=self.name,
            source="compiled",
            finding_type="timeline_event",
            title=f"Evidence timeline: {len(events)} events",
            content=f"Chronological timeline spanning {events[0]['date']} to {events[-1]['date']}",
            data={
                "total_events": len(events),
                "earliest": events[0]["date"],
                "latest": events[-1]["date"],
                "events": events,
            },
            confidence=75,
        )
