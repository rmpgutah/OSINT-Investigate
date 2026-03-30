"""SSL certificate transparency module -- queries crt.sh for certificate logs."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    from duckduckgo_search import DDGS

    _HAS_DDGS = True
except ImportError:
    _HAS_DDGS = False


class SslCertificateModule(BaseModule):
    name = "ssl_certificate"
    description = "SSL/TLS certificate transparency log analysis"

    CRT_SH_URL = "https://crt.sh/?q={domain}&output=json"
    MAX_CERTS = 50

    def applicable_target_types(self) -> list[str]:
        return ["domain", "ip"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        domain = target.domain or target.ip_address or target.label
        if not domain:
            self.logger.info("No domain/IP available on target, skipping SSL cert check")
            return results

        # 1. crt.sh certificate transparency query
        results.extend(await self._query_crt_sh(domain))

        # Summary finding
        anomalies = [r for r in results if r.finding_type == "ssl_anomaly"]
        results.append(
            ModuleResult(
                module_name=self.name,
                source="ssl_certificate",
                finding_type="ssl_summary",
                title=f"SSL certificate summary for {domain}",
                content=(
                    f"Found {len(results)} certificate(s) in transparency logs for "
                    f'"{domain}". {len(anomalies)} anomaly/anomalies detected.'
                ),
                data={
                    "domain": domain,
                    "total_certs": len(results),
                    "anomalies": len(anomalies),
                },
                confidence=65,
            )
        )

        return results

    # ------------------------------------------------------------------
    # crt.sh query
    # ------------------------------------------------------------------

    async def _query_crt_sh(self, domain: str) -> list[ModuleResult]:
        """Query crt.sh certificate transparency logs."""
        results: list[ModuleResult] = []
        url = self.CRT_SH_URL.format(domain=domain)

        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.logger.warning(f"crt.sh request failed: {exc}")
            return results

        if not response:
            self.logger.info("crt.sh returned no response")
            return results

        try:
            certs: list[dict[str, Any]] = response.json()
        except Exception:
            self.logger.warning("Failed to parse crt.sh JSON response")
            return results

        if not isinstance(certs, list):
            self.logger.warning("crt.sh returned unexpected format")
            return results

        seen_serials: set[str] = set()
        now = datetime.now(timezone.utc)

        for cert in certs[: self.MAX_CERTS]:
            serial = str(cert.get("serial_number", ""))
            if serial in seen_serials:
                continue
            if serial:
                seen_serials.add(serial)

            issuer_name = cert.get("issuer_name", "Unknown")
            common_name = cert.get("common_name", "")
            not_before = cert.get("not_before", "")
            not_after = cert.get("not_after", "")
            name_value = cert.get("name_value", "")
            entry_timestamp = cert.get("entry_timestamp", "")

            # Parse subject alternative names
            san_list = [s.strip() for s in name_value.split("\n") if s.strip()] if name_value else []

            # Check for anomalies
            is_expired = False
            is_wildcard = common_name.startswith("*.")
            is_multi_domain = len(san_list) > 5

            if not_after:
                try:
                    expiry = datetime.fromisoformat(not_after.replace("Z", "+00:00"))
                    is_expired = expiry < now
                except (ValueError, TypeError):
                    pass

            # Standard certificate finding
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="crt.sh",
                    finding_type="ssl_certificate",
                    title=f"Certificate for {common_name} issued by {issuer_name}",
                    content=(
                        f"Certificate CN={common_name}, Issuer={issuer_name}, "
                        f"Valid: {not_before} to {not_after}, "
                        f"SANs: {len(san_list)}"
                    ),
                    data={
                        "common_name": common_name,
                        "issuer": issuer_name,
                        "serial_number": serial,
                        "valid_from": not_before,
                        "valid_to": not_after,
                        "subject_alt_names": san_list,
                        "entry_timestamp": entry_timestamp,
                        "is_wildcard": is_wildcard,
                        "is_multi_domain": is_multi_domain,
                        "is_expired": is_expired,
                    },
                    confidence=80,
                )
            )

            # Anomaly findings
            if is_expired:
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="crt.sh",
                        finding_type="ssl_anomaly",
                        title=f"Expired certificate detected for {common_name}",
                        content=(
                            f"Certificate CN={common_name} expired on {not_after}. "
                            f"Issuer: {issuer_name}."
                        ),
                        data={
                            "anomaly_type": "expired",
                            "common_name": common_name,
                            "expired_on": not_after,
                            "issuer": issuer_name,
                        },
                        confidence=70,
                    )
                )

            if is_wildcard:
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="crt.sh",
                        finding_type="ssl_anomaly",
                        title=f"Wildcard certificate: {common_name}",
                        content=(
                            f"Wildcard certificate CN={common_name} covers all "
                            f"subdomains. Issuer: {issuer_name}."
                        ),
                        data={
                            "anomaly_type": "wildcard",
                            "common_name": common_name,
                            "issuer": issuer_name,
                        },
                        confidence=70,
                    )
                )

            if is_multi_domain:
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="crt.sh",
                        finding_type="ssl_anomaly",
                        title=f"Multi-domain certificate: {common_name} ({len(san_list)} SANs)",
                        content=(
                            f"Certificate CN={common_name} covers {len(san_list)} "
                            f"domains via Subject Alternative Names."
                        ),
                        data={
                            "anomaly_type": "multi_domain",
                            "common_name": common_name,
                            "san_count": len(san_list),
                            "subject_alt_names": san_list[:20],
                        },
                        confidence=70,
                    )
                )

        self.logger.info(f"crt.sh returned {len(results)} finding(s) for '{domain}'")
        return results
