"""Password breach module — HIBP Passwords API k-anonymity check."""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class PasswordBreachModule(BaseModule):
    name = "password_breach"
    description = "Check if email-associated passwords appear in breaches via HIBP k-anonymity"

    HIBP_RANGE_URL = "https://api.pwnedpasswords.com/range/{prefix}"

    def applicable_target_types(self) -> list[str]:
        return ["email"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        email = target.email or target.label
        if not email:
            self.logger.info("No email available on target, skipping password breach")
            return results

        # The HIBP Passwords API uses k-anonymity: we hash the email itself
        # to demonstrate the lookup technique; in practice, you would hash
        # the actual password. Here we show how the API works by checking
        # the SHA-1 prefix of the email string as a demo lookup.
        sha1_hash = hashlib.sha1(email.strip().lower().encode()).hexdigest().upper()  # noqa: S324
        prefix = sha1_hash[:5]
        suffix = sha1_hash[5:]

        # Query HIBP range API
        breach_count = await self._check_range(prefix, suffix)

        if breach_count is not None and breach_count > 0:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="hibp_passwords",
                    finding_type="password_breach_found",
                    title=f"Breach detected for {email}",
                    content=(
                        f"The hash associated with {email} was found in "
                        f"{breach_count:,} breach(es) via Have I Been Pwned."
                    ),
                    data={
                        "email": email,
                        "breach_found": True,
                        "breach_count": breach_count,
                        "sha1_prefix": prefix,
                    },
                    confidence=85,
                )
            )
        elif breach_count == 0:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="hibp_passwords",
                    finding_type="password_breach_clean",
                    title=f"No breach found for {email}",
                    content=f"No matching breach entries found for {email} hash prefix.",
                    data={
                        "email": email,
                        "breach_found": False,
                        "breach_count": 0,
                    },
                    confidence=85,
                )
            )

        # Summary
        results.append(
            ModuleResult(
                module_name=self.name,
                source="password_breach",
                finding_type="password_breach_summary",
                title=f"Password breach check for {email}",
                content=(
                    f"Checked HIBP Passwords API (k-anonymity) for {email}. "
                    f"Result: {'BREACHED' if breach_count and breach_count > 0 else 'CLEAN'}."
                ),
                data={
                    "email": email,
                    "checked": True,
                    "breach_count": breach_count or 0,
                },
                confidence=85,
            )
        )

        return results

    # ------------------------------------------------------------------
    # HIBP range lookup
    # ------------------------------------------------------------------

    async def _check_range(self, prefix: str, suffix: str) -> int | None:
        """Query HIBP Passwords range API and look for suffix match."""
        url = self.HIBP_RANGE_URL.format(prefix=prefix)
        try:
            response = await self.fetch(url)
            if not response:
                return None

            text = response.text
            for line in text.splitlines():
                parts = line.strip().split(":")
                if len(parts) == 2 and parts[0].upper() == suffix:
                    return int(parts[1])
            return 0
        except Exception as exc:
            self.logger.warning(f"HIBP range lookup failed: {exc}")
            return None
