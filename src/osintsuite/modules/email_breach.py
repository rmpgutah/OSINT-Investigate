"""Email breach aggregation, disposable detection, and risk scoring module."""

from __future__ import annotations

from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

# ~100 known disposable / temporary email domains
DISPOSABLE_DOMAINS: set[str] = {
    "mailinator.com",
    "guerrillamail.com",
    "tempmail.com",
    "throwaway.email",
    "yopmail.com",
    "sharklasers.com",
    "guerrillamailblock.com",
    "grr.la",
    "10minutemail.com",
    "trashmail.com",
    "tempail.com",
    "fakeinbox.com",
    "mailnesia.com",
    "maildrop.cc",
    "dispostable.com",
    "temp-mail.org",
    "emailondeck.com",
    "getnada.com",
    "burnermail.io",
    "inboxbear.com",
    "mailcatch.com",
    "mintemail.com",
    "mohmal.com",
    "tempinbox.com",
    "mailforspam.com",
    "safetymail.info",
    "trashmail.net",
    "trashmail.me",
    "guerrillamail.info",
    "guerrillamail.net",
    "guerrillamail.org",
    "guerrillamail.de",
    "spam4.me",
    "byom.de",
    "trbvm.com",
    "discard.email",
    "discardmail.com",
    "discardmail.de",
    "emailigo.de",
    "emz.net",
    "getairmail.com",
    "grandmamail.com",
    "harakirimail.com",
    "mailexpire.com",
    "mailnator.com",
    "mailzilla.com",
    "mytemp.email",
    "nobulk.com",
    "nospam.ze.tc",
    "owlpic.com",
    "sharklasers.com",
    "spamfree24.org",
    "tempomail.fr",
    "throwam.com",
    "trash-mail.com",
    "wegwerfmail.de",
    "wegwerfmail.net",
    "yopmail.fr",
    "yopmail.net",
    "jetable.org",
    "nospam.ze.tc",
    "trash-me.com",
    "mailtemp.info",
    "tempmailo.com",
    "tempr.email",
    "tmail.ws",
    "tmpmail.net",
    "tmpmail.org",
    "boun.cr",
    "clrmail.com",
    "crazymailing.com",
    "disposableemailaddresses.emailmiser.com",
    "emailage.cf",
    "emailage.ga",
    "emailage.gq",
    "emailage.ml",
    "emailage.tk",
    "fakemailgenerator.com",
    "gishpuppy.com",
    "guerrillamail.biz",
    "hulapla.de",
    "imgof.com",
    "instantemailaddress.com",
    "klzlk.com",
    "koszmail.pl",
    "kurzepost.de",
    "mailcatch.com",
    "mailme.lv",
    "mailsac.com",
    "meltmail.com",
    "mobi.web.id",
    "objectmail.com",
    "proxymail.eu",
    "rcpt.at",
    "reallymymail.com",
    "recode.me",
    "spaml.de",
    "superrito.com",
    "tittbit.in",
    "trashymail.com",
    "twinmail.de",
    "uggsrock.com",
    "veryrealemail.com",
    "viditag.com",
    "whatpaas.com",
    "wh4f.org",
}


class EmailBreachModule(BaseModule):
    name = "email_breach"
    description = "Email breach aggregation, disposable detection, and risk scoring"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def applicable_target_types(self) -> list[str]:
        return ["email"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        email = target.email or (
            target.label if target.target_type == "email" else None
        )
        if not email:
            return results

        # 1. Disposable check
        disposable_result = await self._check_disposable(email)
        results.append(disposable_result)
        is_disposable = disposable_result.data.get("disposable", False)

        # 2. EmailRep reputation check
        emailrep_data: dict = {}
        emailrep_result = await self._check_emailrep(email)
        if emailrep_result:
            results.append(emailrep_result)
            emailrep_data = emailrep_result.data

        # 3. Risk score
        risk_result = self._compute_risk_score(is_disposable, emailrep_data)
        results.append(risk_result)

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _check_disposable(self, email: str) -> ModuleResult:
        """Check whether the email domain is a known disposable provider."""
        domain = email.split("@", 1)[-1].lower()

        # Local hardcoded check
        local_disposable = domain in DISPOSABLE_DOMAINS

        # Remote check via Kickbox open API
        api_disposable: bool | None = None
        try:
            resp = await self.fetch(
                f"https://open.kickbox.com/v1/disposable/{domain}"
            )
            if resp is not None:
                payload = resp.json()
                api_disposable = payload.get("disposable", None)
        except Exception as exc:
            self.logger.warning("Kickbox disposable check failed: %s", exc)

        disposable = local_disposable or (api_disposable is True)

        return ModuleResult(
            module_name=self.name,
            source="kickbox",
            finding_type="disposable_check",
            title=f"Disposable email: {'Yes' if disposable else 'No'}",
            content=(
                f"Domain {domain} {'is' if disposable else 'is not'} "
                "a known disposable email provider"
            ),
            data={
                "email": email,
                "domain": domain,
                "disposable": disposable,
                "local_match": local_disposable,
                "kickbox_disposable": api_disposable,
            },
            confidence=90,
        )

    async def _check_emailrep(self, email: str) -> ModuleResult | None:
        """Query emailrep.io for reputation and breach data."""
        resp = await self.fetch(
            f"https://emailrep.io/{email}",
            headers={"User-Agent": "OSINT-Suite/0.1"},
        )
        if resp is None:
            return None

        try:
            payload = resp.json()
        except Exception as exc:
            self.logger.warning("Failed to parse emailrep response: %s", exc)
            return None

        details = payload.get("details", {})
        profiles = details.get("profiles", [])
        references = payload.get("references", 0)

        return ModuleResult(
            module_name=self.name,
            source="emailrep.io",
            finding_type="email_reputation",
            title=f"Email reputation: {payload.get('reputation', 'unknown')}",
            content=(
                f"Reputation={payload.get('reputation')}, "
                f"suspicious={payload.get('suspicious')}, "
                f"references={references}, "
                f"profiles={', '.join(profiles) if profiles else 'none'}"
            ),
            data={
                "email": email,
                "reputation": payload.get("reputation"),
                "suspicious": payload.get("suspicious"),
                "references": references,
                "details": {
                    "disposable": details.get("disposable"),
                    "free_provider": details.get("free_provider"),
                    "deliverable": details.get("deliverable"),
                    "valid_mx": details.get("valid_mx"),
                    "spoofable": details.get("spoofable"),
                    "malicious_activity": details.get("malicious_activity"),
                    "credentials_leaked": details.get("credentials_leaked"),
                    "profiles": profiles,
                },
            },
            confidence=75,
            raw_response=resp.text,
        )

    def _compute_risk_score(
        self, disposable: bool, emailrep_data: dict
    ) -> ModuleResult:
        """Compute a weighted 0-100 risk score from collected signals."""
        score = 0
        factors: list[str] = []
        details = emailrep_data.get("details", {})

        # +30 if disposable
        if disposable:
            score += 30
            factors.append("disposable_domain (+30)")

        # +25 if credentials leaked
        if details.get("credentials_leaked"):
            score += 25
            factors.append("credentials_leaked (+25)")

        # +15 if malicious activity
        if details.get("malicious_activity"):
            score += 15
            factors.append("malicious_activity (+15)")

        # +10 if no valid MX
        if details.get("valid_mx") is False:
            score += 10
            factors.append("no_valid_mx (+10)")

        # +10 if spoofable
        if details.get("spoofable"):
            score += 10
            factors.append("spoofable (+10)")

        # +5 per breach reference, capped at 20
        references = emailrep_data.get("references", 0)
        if references and isinstance(references, (int, float)):
            breach_points = min(int(references) * 5, 20)
            if breach_points > 0:
                score += breach_points
                factors.append(f"breach_references x{int(references)} (+{breach_points})")

        # -10 if high reputation
        reputation = emailrep_data.get("reputation")
        if reputation == "high":
            score -= 10
            factors.append("high_reputation (-10)")

        # Clamp to 0-100
        score = max(0, min(100, score))

        # Determine risk level
        if score >= 70:
            risk_level = "critical"
        elif score >= 45:
            risk_level = "high"
        elif score >= 20:
            risk_level = "medium"
        else:
            risk_level = "low"

        return ModuleResult(
            module_name=self.name,
            source="aggregated",
            finding_type="email_risk_score",
            title=f"Email risk score: {score}/100 ({risk_level})",
            content=(
                f"Risk score {score}/100 — {risk_level}. "
                f"Factors: {', '.join(factors) if factors else 'none'}"
            ),
            data={
                "risk_score": score,
                "risk_level": risk_level,
                "factors": factors,
            },
            confidence=70,
        )
