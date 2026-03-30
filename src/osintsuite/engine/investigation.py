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

# v0.2 modules
from osintsuite.modules.shodan_intel import ShodanIntelModule
from osintsuite.modules.email_breach import EmailBreachModule
from osintsuite.modules.username_osint import UsernameOsintModule
from osintsuite.modules.google_dork import GoogleDorkModule
from osintsuite.modules.paste_search import PasteSearchModule

# v0.3 — Batch 1: People & Records
from osintsuite.modules.court_records import CourtRecordsModule
from osintsuite.modules.business_entity import BusinessEntityModule
from osintsuite.modules.property_records import PropertyRecordsModule
from osintsuite.modules.vehicle_lookup import VehicleLookupModule
from osintsuite.modules.sex_offender import SexOffenderModule
from osintsuite.modules.bankruptcy_records import BankruptcyRecordsModule
from osintsuite.modules.ssdi_lookup import SsdiLookupModule
from osintsuite.modules.professional_license import ProfessionalLicenseModule
from osintsuite.modules.nonprofit_lookup import NonprofitLookupModule
from osintsuite.modules.political_donations import PoliticalDonationsModule

# v0.3 — Batch 2: Digital & Cyber
from osintsuite.modules.dns_history import DnsHistoryModule
from osintsuite.modules.subdomain_enum import SubdomainEnumModule
from osintsuite.modules.tech_stack import TechStackModule
from osintsuite.modules.wifi_lookup import WifiLookupModule
from osintsuite.modules.crypto_lookup import CryptoLookupModule
from osintsuite.modules.dark_web import DarkWebModule
from osintsuite.modules.news_monitor import NewsMonitorModule
from osintsuite.modules.github_intel import GithubIntelModule
from osintsuite.modules.phone_disposable import PhoneDisposableModule
from osintsuite.modules.image_hash import ImageHashModule

# v0.3 — Batch 3: Location & Physical
from osintsuite.modules.geolocation import GeolocationModule
from osintsuite.modules.address_validate import AddressValidateModule
from osintsuite.modules.aerial_view import AerialViewModule
from osintsuite.modules.weather_forensics import WeatherForensicsModule
from osintsuite.modules.timezone_forensics import TimezoneForensicsModule
from osintsuite.modules.flight_track import FlightTrackModule
from osintsuite.modules.ship_track import ShipTrackModule
from osintsuite.modules.cell_tower import CellTowerModule
from osintsuite.modules.public_cameras import PublicCamerasModule
from osintsuite.modules.radio_freq import RadioFreqModule

# v0.4 — Life History: Criminal Justice
from osintsuite.modules.criminal_records import CriminalRecordsModule
from osintsuite.modules.warrant_search import WarrantSearchModule
from osintsuite.modules.arrest_records import ArrestRecordsModule
from osintsuite.modules.inmate_search import InmateSearchModule
from osintsuite.modules.parole_probation import ParoleProbationModule

# v0.4 — Life History: Personal Records
from osintsuite.modules.education_history import EducationHistoryModule
from osintsuite.modules.employment_history import EmploymentHistoryModule
from osintsuite.modules.military_records import MilitaryRecordsModule
from osintsuite.modules.marriage_divorce import MarriageDivorceModule
from osintsuite.modules.immigration_records import ImmigrationRecordsModule

# v0.4 — Life History: Financial & Social
from osintsuite.modules.financial_records import FinancialRecordsModule
from osintsuite.modules.associates_network import AssociatesNetworkModule
from osintsuite.modules.real_estate_deep import RealEstateDeepModule
from osintsuite.modules.travel_history import TravelHistoryModule
from osintsuite.modules.alias_detection import AliasDetectionModule

# v0.4 — Life History: Digital & Genealogy
from osintsuite.modules.genealogy_records import GenealogyRecordsModule
from osintsuite.modules.digital_footprint import DigitalFootprintModule
from osintsuite.modules.phone_deep import PhoneDeepModule
from osintsuite.modules.email_deep import EmailDeepModule
from osintsuite.modules.life_event_timeline import LifeEventTimelineModule

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

        # v0.2 — advanced OSINT modules
        self.modules["shodan_intel"] = ShodanIntelModule(
            client, limiter, shodan_api_key=self.settings.shodan_api_key
        )
        self.modules["email_breach"] = EmailBreachModule(client, limiter)
        self.modules["username_osint"] = UsernameOsintModule(client, limiter)
        self.modules["google_dork"] = GoogleDorkModule(client, limiter)
        self.modules["paste_search"] = PasteSearchModule(client, limiter)

        # v0.3 — Batch 1: People & Records
        self.modules["court_records"] = CourtRecordsModule(client, limiter)
        self.modules["business_entity"] = BusinessEntityModule(client, limiter)
        self.modules["property_records"] = PropertyRecordsModule(client, limiter)
        self.modules["vehicle_lookup"] = VehicleLookupModule(client, limiter)
        self.modules["sex_offender"] = SexOffenderModule(client, limiter)
        self.modules["bankruptcy_records"] = BankruptcyRecordsModule(client, limiter)
        self.modules["ssdi_lookup"] = SsdiLookupModule(client, limiter)
        self.modules["professional_license"] = ProfessionalLicenseModule(client, limiter)
        self.modules["nonprofit_lookup"] = NonprofitLookupModule(client, limiter)
        self.modules["political_donations"] = PoliticalDonationsModule(client, limiter)

        # v0.3 — Batch 2: Digital & Cyber
        self.modules["dns_history"] = DnsHistoryModule(client, limiter)
        self.modules["subdomain_enum"] = SubdomainEnumModule(client, limiter)
        self.modules["tech_stack"] = TechStackModule(client, limiter)
        self.modules["wifi_lookup"] = WifiLookupModule(client, limiter)
        self.modules["crypto_lookup"] = CryptoLookupModule(client, limiter)
        self.modules["dark_web"] = DarkWebModule(client, limiter)
        self.modules["news_monitor"] = NewsMonitorModule(client, limiter)
        self.modules["github_intel"] = GithubIntelModule(client, limiter)
        self.modules["phone_disposable"] = PhoneDisposableModule(client, limiter)
        self.modules["image_hash"] = ImageHashModule(client, limiter)

        # v0.3 — Batch 3: Location & Physical
        self.modules["geolocation"] = GeolocationModule(client, limiter)
        self.modules["address_validate"] = AddressValidateModule(client, limiter)
        self.modules["aerial_view"] = AerialViewModule(client, limiter)
        self.modules["weather_forensics"] = WeatherForensicsModule(client, limiter)
        self.modules["timezone_forensics"] = TimezoneForensicsModule(client, limiter)
        self.modules["flight_track"] = FlightTrackModule(client, limiter)
        self.modules["ship_track"] = ShipTrackModule(client, limiter)
        self.modules["cell_tower"] = CellTowerModule(client, limiter)
        self.modules["public_cameras"] = PublicCamerasModule(client, limiter)
        self.modules["radio_freq"] = RadioFreqModule(client, limiter)

        # v0.4 — Life History: Criminal Justice
        self.modules["criminal_records"] = CriminalRecordsModule(client, limiter)
        self.modules["warrant_search"] = WarrantSearchModule(client, limiter)
        self.modules["arrest_records"] = ArrestRecordsModule(client, limiter)
        self.modules["inmate_search"] = InmateSearchModule(client, limiter)
        self.modules["parole_probation"] = ParoleProbationModule(client, limiter)

        # v0.4 — Life History: Personal Records
        self.modules["education_history"] = EducationHistoryModule(client, limiter)
        self.modules["employment_history"] = EmploymentHistoryModule(client, limiter)
        self.modules["military_records"] = MilitaryRecordsModule(client, limiter)
        self.modules["marriage_divorce"] = MarriageDivorceModule(client, limiter)
        self.modules["immigration_records"] = ImmigrationRecordsModule(client, limiter)

        # v0.4 — Life History: Financial & Social
        self.modules["financial_records"] = FinancialRecordsModule(client, limiter)
        self.modules["associates_network"] = AssociatesNetworkModule(client, limiter)
        self.modules["real_estate_deep"] = RealEstateDeepModule(client, limiter)
        self.modules["travel_history"] = TravelHistoryModule(client, limiter)
        self.modules["alias_detection"] = AliasDetectionModule(client, limiter)

        # v0.4 — Life History: Digital & Genealogy
        self.modules["genealogy_records"] = GenealogyRecordsModule(client, limiter)
        self.modules["digital_footprint"] = DigitalFootprintModule(client, limiter)
        self.modules["phone_deep"] = PhoneDeepModule(client, limiter)
        self.modules["email_deep"] = EmailDeepModule(client, limiter)
        self.modules["life_event_timeline"] = LifeEventTimelineModule(client, limiter)

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
