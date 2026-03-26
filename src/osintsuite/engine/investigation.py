"""Investigation orchestrator — wires modules to the database layer.

Both CLI and Web interfaces call this engine. It has no knowledge of
HTTP endpoints or terminal I/O.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID

import httpx

from osintsuite.config import Settings
from osintsuite.db.models import Finding
from osintsuite.db.repository import Repository
from osintsuite.modules.base import BaseModule, RateLimiter
from osintsuite.modules.domain_recon import DomainReconModule
from osintsuite.modules.email_intel import EmailIntelModule
from osintsuite.modules.hash_intel import HashIntelModule
from osintsuite.modules.ip_forensics import IpForensicsModule
from osintsuite.modules.metadata_forensics import MetadataForensicsModule
from osintsuite.modules.person_search import PersonSearchModule
from osintsuite.modules.phone_lookup import PhoneLookupModule
from osintsuite.modules.social_media import SocialMediaModule
from osintsuite.modules.timeline_forensics import TimelineForensicsModule
from osintsuite.modules.web_scraper import WebScraperModule

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class InvestigationEngine:
    """Core engine that coordinates module execution and data persistence."""

    def __init__(self, db: Repository, settings: Settings):
        self.db = db
        self.settings = settings
        self.modules: dict[str, BaseModule] = {}
        self._setup_modules()

    def _setup_modules(self):
        """Register all available modules with shared HTTP client and rate limiter."""
        client = httpx.AsyncClient(
            timeout=self.settings.http_timeout,
            headers={"User-Agent": self.settings.user_agent},
            follow_redirects=True,
        )
        limiter = RateLimiter(self.settings.http_rate_limit_per_second)

        self.modules["person_search"] = PersonSearchModule(client, limiter)
        self.modules["web_scraper"] = WebScraperModule(client, limiter)
        self.modules["email_intel"] = EmailIntelModule(
            client, limiter, hibp_api_key=self.settings.hibp_api_key
        )
        self.modules["phone_lookup"] = PhoneLookupModule(client, limiter)
        self.modules["domain_recon"] = DomainReconModule(client, limiter)
        self.modules["social_media"] = SocialMediaModule(client, limiter)

        # Forensics modules
        self.modules["ip_forensics"] = IpForensicsModule(client, limiter)
        self.modules["metadata_forensics"] = MetadataForensicsModule(client, limiter)
        self.modules["hash_intel"] = HashIntelModule(
            client, limiter,
            vt_api_key=self.settings.virustotal_api_key,
            abuseipdb_api_key=self.settings.abuseipdb_api_key,
        )
        self.modules["timeline_forensics"] = TimelineForensicsModule(client, limiter)

    def list_modules(self) -> dict[str, str]:
        """Return module names and descriptions."""
        return {name: mod.description for name, mod in self.modules.items()}

    async def run_module(
        self, target_id: UUID, module_name: str
    ) -> list[Finding]:
        """Run a single module against a target and persist results."""
        target = await self.db.get_target(target_id)
        if not target:
            raise ValueError(f"Target {target_id} not found")

        module = self.modules.get(module_name)
        if not module:
            raise ValueError(f"Module '{module_name}' not found")

        if target.target_type not in module.applicable_target_types():
            raise ValueError(
                f"Module '{module_name}' cannot process target type '{target.target_type}'"
            )

        run = await self.db.create_module_run(target_id, module_name)
        try:
            results = await module.run(target)
            findings = await self.db.save_findings(target_id, results)
            await self.db.complete_module_run(run.id, len(findings))
            logger.info(
                f"Module '{module_name}' completed for target {target.label}: "
                f"{len(findings)} findings"
            )
            return findings
        except Exception as e:
            await self.db.fail_module_run(run.id, str(e))
            logger.error(f"Module '{module_name}' failed for target {target.label}: {e}")
            raise

    async def run_all_applicable(
        self, target_id: UUID
    ) -> dict[str, list[Finding]]:
        """Run every module applicable to the target's type."""
        target = await self.db.get_target(target_id)
        if not target:
            raise ValueError(f"Target {target_id} not found")

        results: dict[str, list[Finding]] = {}
        for name, module in self.modules.items():
            if target.target_type in module.applicable_target_types():
                try:
                    findings = await self.run_module(target_id, name)
                    results[name] = findings
                except Exception as e:
                    logger.error(f"Module '{name}' failed: {e}")
                    results[name] = []

        return results

    async def close(self):
        """Clean up resources."""
        for module in self.modules.values():
            if hasattr(module, "http") and hasattr(module.http, "aclose"):
                await module.http.aclose()
