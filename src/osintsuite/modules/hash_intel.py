"""Hash & threat intelligence module — URLhaus, ThreatFox, VirusTotal, AbuseIPDB."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class HashIntelModule(BaseModule):
    name = "hash_intel"
    description = "Malware, IOC, and threat intelligence lookups (URLhaus, ThreatFox, VirusTotal)"

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        rate_limiter,
        vt_api_key: str | None = None,
        abuseipdb_api_key: str | None = None,
    ):
        super().__init__(http_client, rate_limiter)
        self.vt_api_key = vt_api_key
        self.abuseipdb_api_key = abuseipdb_api_key

    def applicable_target_types(self) -> list[str]:
        return ["domain", "ip"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        indicator = target.label

        if "://" in indicator:
            indicator = indicator.split("://")[1].split("/")[0]

        results.extend(await self._urlhaus_check(indicator))
        results.extend(await self._threatfox_check(indicator))

        if self.vt_api_key:
            results.extend(await self._virustotal_check(indicator, target.target_type))

        if self.abuseipdb_api_key and target.target_type == "ip":
            results.extend(await self._abuseipdb_check(indicator))

        return results

    async def _urlhaus_check(self, indicator: str) -> list[ModuleResult]:
        """Check URLhaus for known malware distribution from this host."""
        await self.limiter.acquire()
        try:
            resp = await self.http.post(
                "https://urlhaus-api.abuse.ch/v1/host/",
                data={"host": indicator},
                timeout=10,
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            if data.get("query_status") != "no_results":
                url_count = data.get("url_count", 0)
                urls = data.get("urls", [])[:10]  # Cap at 10

                return [
                    ModuleResult(
                        module_name=self.name,
                        source="urlhaus",
                        finding_type="malware_association",
                        title=f"URLhaus: {url_count} malware URLs from {indicator}",
                        content=f"{url_count} malicious URLs associated with this host",
                        data={
                            "indicator": indicator,
                            "url_count": url_count,
                            "urls": [
                                {
                                    "url": u.get("url"),
                                    "url_status": u.get("url_status"),
                                    "threat": u.get("threat"),
                                    "date_added": u.get("date_added"),
                                    "tags": u.get("tags"),
                                }
                                for u in urls
                            ],
                        },
                        confidence=85,
                    )
                ]
        except Exception as e:
            self.logger.debug(f"URLhaus check failed for {indicator}: {e}")

        return []

    async def _threatfox_check(self, indicator: str) -> list[ModuleResult]:
        """Check ThreatFox for known IOCs matching this indicator."""
        await self.limiter.acquire()
        try:
            resp = await self.http.post(
                "https://threatfox-api.abuse.ch/api/v1/",
                json={"query": "search_ioc", "search_term": indicator},
                timeout=10,
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            if data.get("query_status") == "ok" and data.get("data"):
                iocs = data["data"][:10]

                return [
                    ModuleResult(
                        module_name=self.name,
                        source="threatfox",
                        finding_type="ioc_match",
                        title=f"ThreatFox: {len(data['data'])} IOCs for {indicator}",
                        content=f"Matched {len(data['data'])} indicators of compromise",
                        data={
                            "indicator": indicator,
                            "total_iocs": len(data["data"]),
                            "iocs": [
                                {
                                    "ioc": i.get("ioc"),
                                    "threat_type": i.get("threat_type"),
                                    "malware": i.get("malware"),
                                    "confidence_level": i.get("confidence_level"),
                                    "first_seen": i.get("first_seen_utc"),
                                    "last_seen": i.get("last_seen_utc"),
                                    "tags": i.get("tags"),
                                }
                                for i in iocs
                            ],
                        },
                        confidence=90,
                    )
                ]
        except Exception as e:
            self.logger.debug(f"ThreatFox check failed for {indicator}: {e}")

        return []

    async def _virustotal_check(self, indicator: str, target_type: str) -> list[ModuleResult]:
        """Query VirusTotal for domain or IP report (free tier: 4 req/min)."""
        if not self.vt_api_key:
            return []

        await self.limiter.acquire()
        endpoint = "domains" if target_type == "domain" else "ip_addresses"
        url = f"https://www.virustotal.com/api/v3/{endpoint}/{indicator}"

        try:
            resp = await self.http.get(
                url,
                headers={"x-apikey": self.vt_api_key},
                timeout=15,
            )
            if resp.status_code != 200:
                return []

            data = resp.json()
            attrs = data.get("data", {}).get("attributes", {})

            # Analysis stats
            stats = attrs.get("last_analysis_stats", {})
            malicious = stats.get("malicious", 0)
            suspicious = stats.get("suspicious", 0)
            total = sum(stats.values()) if stats else 0

            reputation = attrs.get("reputation", 0)

            confidence = 90 if malicious > 0 else 60

            return [
                ModuleResult(
                    module_name=self.name,
                    source="virustotal",
                    finding_type="vt_report",
                    title=f"VirusTotal: {malicious}/{total} detections for {indicator}",
                    content=f"Malicious: {malicious} | Suspicious: {suspicious} | Total engines: {total} | Reputation: {reputation}",
                    data={
                        "indicator": indicator,
                        "analysis_stats": stats,
                        "reputation": reputation,
                        "categories": attrs.get("categories", {}),
                        "registrar": attrs.get("registrar"),
                        "creation_date": attrs.get("creation_date"),
                        "last_analysis_date": attrs.get("last_analysis_date"),
                        "whois": attrs.get("whois", "")[:500],  # Truncate
                    },
                    confidence=confidence,
                )
            ]
        except Exception as e:
            self.logger.debug(f"VirusTotal check failed for {indicator}: {e}")

        return []

    async def _abuseipdb_check(self, ip: str) -> list[ModuleResult]:
        """Check AbuseIPDB for abuse reports on an IP address."""
        if not self.abuseipdb_api_key:
            return []

        await self.limiter.acquire()
        try:
            resp = await self.http.get(
                "https://api.abuseipdb.com/api/v2/check",
                params={"ipAddress": ip, "maxAgeInDays": 90},
                headers={
                    "Key": self.abuseipdb_api_key,
                    "Accept": "application/json",
                },
                timeout=10,
            )
            if resp.status_code != 200:
                return []

            data = resp.json().get("data", {})
            abuse_score = data.get("abuseConfidenceScore", 0)
            total_reports = data.get("totalReports", 0)

            if total_reports == 0 and abuse_score == 0:
                return []

            return [
                ModuleResult(
                    module_name=self.name,
                    source="abuseipdb",
                    finding_type="abuse_report",
                    title=f"AbuseIPDB: {abuse_score}% confidence, {total_reports} reports",
                    content=f"Abuse score: {abuse_score}% | Reports: {total_reports} | ISP: {data.get('isp', 'Unknown')} | Country: {data.get('countryCode', 'Unknown')}",
                    data={
                        "ip": ip,
                        "abuse_confidence_score": abuse_score,
                        "total_reports": total_reports,
                        "is_public": data.get("isPublic"),
                        "isp": data.get("isp"),
                        "domain": data.get("domain"),
                        "country_code": data.get("countryCode"),
                        "usage_type": data.get("usageType"),
                        "is_tor": data.get("isTor"),
                        "is_whitelisted": data.get("isWhitelisted"),
                        "last_reported_at": data.get("lastReportedAt"),
                    },
                    confidence=min(95, abuse_score + 20),
                )
            ]
        except Exception as e:
            self.logger.debug(f"AbuseIPDB check failed for {ip}: {e}")

        return []
