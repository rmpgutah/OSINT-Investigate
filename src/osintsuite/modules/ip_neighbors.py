"""IP neighbors module — BGP/ASN lookup and subnet neighbor analysis."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class IpNeighborsModule(BaseModule):
    name = "ip_neighbors"
    description = "BGP ASN lookup and IP subnet neighbor analysis"

    BGPVIEW_API = "https://api.bgpview.io/ip/{ip}"
    INTERNETDB_API = "https://internetdb.shodan.io/{ip}"

    def applicable_target_types(self) -> list[str]:
        return ["ip"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        ip = target.label
        if not ip:
            self.logger.info("No IP available on target, skipping IP neighbors")
            return results

        # 1. BGPView ASN lookup
        results.extend(await self._bgpview_lookup(ip))

        # 2. Shodan InternetDB for neighboring IPs
        results.extend(await self._probe_neighbors(ip))

        # Summary finding
        results.append(
            ModuleResult(
                module_name=self.name,
                source="ip_neighbors",
                finding_type="bgp_summary",
                title=f"IP neighbors summary for {ip}",
                content=(
                    f"Found {len(results)} BGP/neighbor result(s) for {ip}."
                ),
                data={
                    "ip": ip,
                    "total_results": len(results),
                },
                confidence=70,
            )
        )

        return results

    # ------------------------------------------------------------------
    # BGPView lookup
    # ------------------------------------------------------------------

    async def _bgpview_lookup(self, ip: str) -> list[ModuleResult]:
        """Query BGPView API for ASN and prefix information."""
        results: list[ModuleResult] = []
        url = self.BGPVIEW_API.format(ip=ip)

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"BGPView API request failed: {exc}")
            return results

        if not response or response.status_code != 200:
            return results

        try:
            data = response.json()
        except Exception:
            self.logger.warning("Failed to parse BGPView JSON response")
            return results

        ip_data = data.get("data", {})
        prefixes = ip_data.get("prefixes", [])

        for prefix_info in prefixes[:5]:
            prefix = prefix_info.get("prefix", "")
            asn_info = prefix_info.get("asn", {})
            asn = asn_info.get("asn", "")
            asn_name = asn_info.get("name", "")
            asn_description = asn_info.get("description", "")
            country = asn_info.get("country_code", "")

            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="bgpview",
                    finding_type="asn_info",
                    title=f"ASN {asn} — {asn_name} ({prefix})",
                    content=(
                        f"IP {ip} belongs to AS{asn} ({asn_name}). "
                        f"Prefix: {prefix}. Country: {country}. "
                        f"Description: {asn_description}"
                    ),
                    data={
                        "ip": ip,
                        "asn": asn,
                        "asn_name": asn_name,
                        "asn_description": asn_description,
                        "prefix": prefix,
                        "country": country,
                        "source": "bgpview",
                    },
                    confidence=80,
                )
            )

        return results

    # ------------------------------------------------------------------
    # Neighbor probing via Shodan InternetDB
    # ------------------------------------------------------------------

    async def _probe_neighbors(self, ip: str) -> list[ModuleResult]:
        """Probe random IPs in the same /24 subnet via Shodan InternetDB."""
        results: list[ModuleResult] = []

        # Parse IP and generate neighbors in /24
        parts = ip.split(".")
        if len(parts) != 4:
            self.logger.warning(f"Cannot parse IP for neighbor probing: {ip}")
            return results

        try:
            base = ".".join(parts[:3])
            current_octet = int(parts[3])
        except ValueError:
            return results

        # Pick 5 random neighbors (avoid .0, .255, and current)
        candidates = [i for i in range(1, 255) if i != current_octet]
        neighbor_ips = [f"{base}.{o}" for o in random.sample(candidates, min(5, len(candidates)))]

        for neighbor_ip in neighbor_ips:
            url = self.INTERNETDB_API.format(ip=neighbor_ip)
            try:
                response = await self.fetch(url)
                if not response or response.status_code != 200:
                    continue

                data: dict[str, Any] = response.json()
                ports = data.get("ports", [])
                hostnames = data.get("hostnames", [])
                cpes = data.get("cpes", [])
                vulns = data.get("vulns", [])

                if ports or hostnames:
                    results.append(
                        ModuleResult(
                            module_name=self.name,
                            source="shodan_internetdb",
                            finding_type="ip_neighbor",
                            title=f"Neighbor {neighbor_ip}: {len(ports)} open port(s)",
                            content=(
                                f"Neighbor IP {neighbor_ip} has {len(ports)} open port(s): "
                                f"{', '.join(str(p) for p in ports[:10])}. "
                                f"Hostnames: {', '.join(hostnames[:5]) if hostnames else 'none'}."
                            ),
                            data={
                                "ip": neighbor_ip,
                                "ports": ports,
                                "hostnames": hostnames,
                                "cpes": cpes[:5],
                                "vulns": vulns[:5],
                                "source": "shodan_internetdb",
                            },
                            confidence=65,
                        )
                    )
            except Exception as exc:
                self.logger.debug(f"InternetDB probe failed for {neighbor_ip}: {exc}")

        return results
