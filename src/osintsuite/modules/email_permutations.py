"""Email permutations module — generates email variants and checks Gravatar."""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class EmailPermutationsModule(BaseModule):
    name = "email_permutations"
    description = "Generate email permutations from name and check Gravatar presence"

    GRAVATAR_URL = "https://www.gravatar.com/avatar/{md5}?d=404&s=1"

    DOMAINS = [
        "gmail.com",
        "yahoo.com",
        "outlook.com",
        "protonmail.com",
        "hotmail.com",
    ]

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        full_name = target.full_name or target.label
        if not full_name:
            self.logger.info("No name available on target, skipping email permutations")
            return results

        parts = full_name.strip().lower().split()
        if len(parts) < 2:
            self.logger.info("Need at least first and last name for permutations")
            return results

        first = parts[0]
        last = parts[-1]
        fi = first[0] if first else ""
        li = last[0] if last else ""

        # Generate permutations
        patterns = [
            f"{first}.{last}",
            f"{first}{last}",
            f"{fi}{last}",
            f"{first}{li}",
            f"{last}{first}",
            f"{last}.{first}",
            f"{first}_{last}",
            f"{fi}.{last}",
            f"{first}.{li}",
            f"{fi}_{last}",
        ]

        emails: list[str] = []
        for pattern in patterns:
            for domain in self.DOMAINS:
                emails.append(f"{pattern}@{domain}")

        # Check Gravatar for each (batch with concurrency limit)
        gravatar_hits: list[dict[str, Any]] = []
        checked = 0

        # Check in batches of 10
        for i in range(0, len(emails), 10):
            batch = emails[i : i + 10]
            tasks = [self._check_gravatar(email) for email in batch]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)
            for email, result in zip(batch, batch_results):
                checked += 1
                if isinstance(result, bool) and result:
                    gravatar_hits.append({"email": email, "has_gravatar": True})

        # Report Gravatar hits
        for hit in gravatar_hits:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="gravatar",
                    finding_type="email_gravatar_match",
                    title=f"Gravatar found: {hit['email']}",
                    content=f"Email {hit['email']} has an associated Gravatar profile.",
                    data=hit,
                    confidence=70,
                )
            )

        # All generated permutations as a finding
        results.append(
            ModuleResult(
                module_name=self.name,
                source="email_permutations",
                finding_type="email_permutations_list",
                title=f"Generated {len(emails)} email permutations for {full_name}",
                content=(
                    f"Generated {len(emails)} email permutations across "
                    f"{len(self.DOMAINS)} providers. Checked Gravatar for all. "
                    f"{len(gravatar_hits)} hit(s) found."
                ),
                data={
                    "name": full_name,
                    "total_permutations": len(emails),
                    "gravatar_hits": len(gravatar_hits),
                    "emails_checked": checked,
                    "sample_emails": emails[:20],
                },
                confidence=50,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Gravatar check
    # ------------------------------------------------------------------

    async def _check_gravatar(self, email: str) -> bool:
        """Check if an email has a Gravatar profile (returns True if found)."""
        md5 = hashlib.md5(email.strip().lower().encode()).hexdigest()  # noqa: S324
        url = self.GRAVATAR_URL.format(md5=md5)
        try:
            response = await self.fetch(url)
            return response is not None
        except Exception:
            return False
