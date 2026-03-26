"""IP forensics module — geolocation, reverse DNS, ASN, abuse, and blacklist checks."""

from __future__ import annotations

import ipaddress
import json
import socket
from typing import TYPE_CHECKING

import dns.resolver
import dns.reversename

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class IpForensicsModule(BaseModule):
    name = "ip_forensics"
    description = "IP geolocation, reverse DNS, ASN lookup, abuse reports, and blacklist checks"

    def applicable_target_types(self) -> list[str]:
        return ["ip", "domain"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        # Resolve IP from domain if needed
        ip = target.label
        if target.target_type == "domain":
            ip = await self._resolve_domain(target.label)
            if not ip:
                return results

        # Validate IP
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            return results

        results.extend(await self._reverse_dns(ip))
        results.extend(await self._geolocation(ip))
        results.extend(await self._blacklist_check(ip))

        return results

    async def _resolve_domain(self, domain: str) -> str | None:
        """Resolve a domain to its first A record."""
        if "://" in domain:
            domain = domain.split("://")[1].split("/")[0]
        try:
            resolver = dns.resolver.Resolver()
            resolver.timeout = 5
            resolver.lifetime = 5
            answers = resolver.resolve(domain, "A")
            return str(answers[0])
        except Exception as e:
            self.logger.debug(f"Could not resolve {domain}: {e}")
            return None

    async def _reverse_dns(self, ip: str) -> list[ModuleResult]:
        """Perform reverse DNS (PTR) lookup."""
        try:
            rev_name = dns.reversename.from_address(ip)
            resolver = dns.resolver.Resolver()
            resolver.timeout = 5
            resolver.lifetime = 5
            answers = resolver.resolve(rev_name, "PTR")
            hostnames = [str(r).rstrip(".") for r in answers]

            return [
                ModuleResult(
                    module_name=self.name,
                    source="dns_ptr",
                    finding_type="reverse_dns",
                    title=f"Reverse DNS for {ip}",
                    content=", ".join(hostnames),
                    data={"ip": ip, "hostnames": hostnames},
                    confidence=90,
                )
            ]
        except Exception as e:
            self.logger.debug(f"PTR lookup failed for {ip}: {e}")
            return []

    async def _geolocation(self, ip: str) -> list[ModuleResult]:
        """IP geolocation and ASN info via ip-api.com (free, no key needed)."""
        url = f"http://ip-api.com/json/{ip}?fields=status,message,country,countryCode,region,regionName,city,zip,lat,lon,timezone,isp,org,as,asname,query"
        resp = await self.fetch(url)
        if not resp:
            return []

        try:
            data = resp.json()
        except Exception:
            return []

        if data.get("status") != "success":
            return []

        results = []

        # Geolocation finding
        geo_parts = [
            data.get("city"),
            data.get("regionName"),
            data.get("country"),
        ]
        location = ", ".join(p for p in geo_parts if p)
        results.append(
            ModuleResult(
                module_name=self.name,
                source="ip-api.com",
                finding_type="ip_geolocation",
                title=f"Geolocation: {ip}",
                content=f"{location} ({data.get('lat')}, {data.get('lon')})",
                data={
                    "ip": ip,
                    "city": data.get("city"),
                    "region": data.get("regionName"),
                    "country": data.get("country"),
                    "country_code": data.get("countryCode"),
                    "lat": data.get("lat"),
                    "lon": data.get("lon"),
                    "timezone": data.get("timezone"),
                    "zip": data.get("zip"),
                },
                confidence=75,
            )
        )

        # ASN / network info
        asn = data.get("as", "")
        isp = data.get("isp", "")
        org = data.get("org", "")
        if asn or isp:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="ip-api.com",
                    finding_type="asn_info",
                    title=f"ASN: {asn}",
                    content=f"ISP: {isp} | Org: {org}",
                    data={
                        "ip": ip,
                        "asn": asn,
                        "asname": data.get("asname"),
                        "isp": isp,
                        "org": org,
                    },
                    confidence=85,
                )
            )

        return results

    async def _blacklist_check(self, ip: str) -> list[ModuleResult]:
        """Check IP against DNS-based blacklists (DNSBL)."""
        # Reverse the IP octets for DNSBL queries
        parts = ip.split(".")
        if len(parts) != 4:
            return []  # IPv6 not supported by most DNSBLs
        reversed_ip = ".".join(reversed(parts))

        dnsbls = {
            "zen.spamhaus.org": "Spamhaus ZEN",
            "bl.spamcop.net": "SpamCop",
            "dnsbl.sorbs.net": "SORBS",
            "b.barracudacentral.org": "Barracuda",
        }

        listed_on = []
        resolver = dns.resolver.Resolver()
        resolver.timeout = 3
        resolver.lifetime = 3

        for dnsbl, label in dnsbls.items():
            query = f"{reversed_ip}.{dnsbl}"
            try:
                resolver.resolve(query, "A")
                listed_on.append(label)
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
                continue
            except Exception:
                continue

        if listed_on:
            return [
                ModuleResult(
                    module_name=self.name,
                    source="dnsbl",
                    finding_type="blacklist_match",
                    title=f"Blacklisted: {ip}",
                    content=f"Listed on: {', '.join(listed_on)}",
                    data={
                        "ip": ip,
                        "blacklists": listed_on,
                        "total_checked": len(dnsbls),
                        "total_listed": len(listed_on),
                    },
                    confidence=80,
                )
            ]

        return []
