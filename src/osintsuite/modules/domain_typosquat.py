"""Domain typosquatting module — generates typosquat variations and checks DNS."""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class DomainTyposquatModule(BaseModule):
    name = "domain_typosquat"
    description = "Typosquatting detection — generates domain variations and checks DNS resolution"

    HOMOGLYPHS = {
        "a": ["@", "4"],
        "e": ["3"],
        "i": ["1", "l"],
        "l": ["1", "i"],
        "o": ["0"],
        "s": ["5", "$"],
        "t": ["7"],
    }

    ALTERNATE_TLDS = [".com", ".net", ".org", ".co", ".io", ".info", ".biz", ".xyz"]

    def applicable_target_types(self) -> list[str]:
        return ["domain"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        domain = target.domain or target.label
        if not domain:
            self.logger.info("No domain available on target, skipping typosquat")
            return results

        # Clean domain
        domain = domain.split("//")[-1].split("/")[0].strip().lower()

        # Split into name and TLD
        parts = domain.rsplit(".", 1)
        if len(parts) != 2:
            self.logger.info(f"Cannot split domain '{domain}' into name.tld")
            return results

        name, tld = parts[0], f".{parts[1]}"

        # Generate variations
        variations = self._generate_variations(name, tld)

        # Check DNS resolution (concurrently, batched)
        resolving: list[dict[str, Any]] = []
        non_resolving = 0

        for i in range(0, len(variations), 20):
            batch = variations[i : i + 20]
            tasks = [asyncio.to_thread(self._check_dns, v) for v in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for variant, result in zip(batch, batch_results):
                if isinstance(result, str) and result:
                    resolving.append({"domain": variant, "ip": result})
                else:
                    non_resolving += 1

        # Report resolving typosquats
        for entry in resolving:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="dns_check",
                    finding_type="typosquat_active",
                    title=f"Active typosquat: {entry['domain']} -> {entry['ip']}",
                    content=(
                        f"Typosquat domain {entry['domain']} resolves to "
                        f"{entry['ip']}. This may be impersonating {domain}."
                    ),
                    data={
                        "original_domain": domain,
                        "typosquat_domain": entry["domain"],
                        "resolved_ip": entry["ip"],
                        "active": True,
                    },
                    confidence=80,
                )
            )

        # Summary
        results.append(
            ModuleResult(
                module_name=self.name,
                source="domain_typosquat",
                finding_type="typosquat_summary",
                title=f"Typosquat analysis for {domain}",
                content=(
                    f"Generated {len(variations)} typosquat variations for {domain}. "
                    f"{len(resolving)} active (resolving), "
                    f"{non_resolving} inactive."
                ),
                data={
                    "domain": domain,
                    "total_variations": len(variations),
                    "active_count": len(resolving),
                    "inactive_count": non_resolving,
                    "active_domains": [e["domain"] for e in resolving],
                },
                confidence=80,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Variation generators
    # ------------------------------------------------------------------

    def _generate_variations(self, name: str, tld: str) -> list[str]:
        variations: set[str] = set()

        # 1. Missing character
        for i in range(len(name)):
            v = name[:i] + name[i + 1 :]
            if v:
                variations.add(v + tld)

        # 2. Swapped adjacent characters
        for i in range(len(name) - 1):
            v = name[:i] + name[i + 1] + name[i] + name[i + 2 :]
            variations.add(v + tld)

        # 3. Doubled character
        for i in range(len(name)):
            v = name[:i] + name[i] * 2 + name[i + 1 :]
            variations.add(v + tld)

        # 4. Wrong TLD
        for alt_tld in self.ALTERNATE_TLDS:
            if alt_tld != tld:
                variations.add(name + alt_tld)

        # 5. Homoglyphs (first occurrence only to keep set manageable)
        for i, char in enumerate(name):
            if char in self.HOMOGLYPHS:
                for replacement in self.HOMOGLYPHS[char]:
                    v = name[:i] + replacement + name[i + 1 :]
                    variations.add(v + tld)

        # 6. Hyphen insertion/removal
        if "-" in name:
            variations.add(name.replace("-", "") + tld)
        for i in range(1, len(name)):
            variations.add(name[:i] + "-" + name[i:] + tld)

        # Remove the original domain
        original = name + tld
        variations.discard(original)

        return sorted(variations)[:100]  # cap at 100

    # ------------------------------------------------------------------
    # DNS check
    # ------------------------------------------------------------------

    @staticmethod
    def _check_dns(domain: str) -> str:
        """Resolve domain to IP. Returns IP string or empty string."""
        try:
            result = socket.getaddrinfo(domain, None, socket.AF_INET, socket.SOCK_STREAM)
            if result:
                return result[0][4][0]
        except (socket.gaierror, OSError):
            pass
        return ""
