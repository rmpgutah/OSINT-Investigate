"""Detect disposable, VoIP, and burner phone numbers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    import phonenumbers
    from phonenumbers import carrier, geocoder, number_type, timezone

    _HAS_PHONENUMBERS = True
except ImportError:
    _HAS_PHONENUMBERS = False

# Known VoIP / disposable carrier names (case-insensitive matching)
VOIP_CARRIERS: set[str] = {
    "google voice",
    "google",
    "textnow",
    "pinger",
    "bandwidth",
    "bandwidth.com",
    "twilio",
    "vonage",
    "nexmo",
    "plivo",
    "telnyx",
    "sinch",
    "textfree",
    "talkatone",
    "grasshopper",
    "ringcentral",
    "ooma",
    "magicjack",
    "freedompop",
    "line2",
    "hushed",
    "burner",
    "sideline",
    "openphone",
    "dialpad",
    "zoom phone",
    "8x8",
    "nextiva",
    "freshcaller",
    "skype",
    "whatsapp",
    "textme",
    "text me",
    "dingtone",
    "2ndline",
    "second line",
    "coverme",
    "phoner",
    "numero esim",
    "telos",
    "flyp",
}


class PhoneDisposableModule(BaseModule):
    name = "phone_disposable"
    description = "Detect disposable, VoIP, and burner phone numbers"

    def applicable_target_types(self) -> list[str]:
        return ["phone"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        if not _HAS_PHONENUMBERS:
            self.logger.warning(
                "phonenumbers library is not installed — skipping phone_disposable"
            )
            return [
                ModuleResult(
                    module_name=self.name,
                    source="phonenumbers",
                    finding_type="phone_analysis",
                    title="Phone disposable module unavailable",
                    content="Install the phonenumbers library to enable this module.",
                    data={"error": "phonenumbers not installed"},
                    confidence=0,
                )
            ]

        results: list[ModuleResult] = []
        raw_number = target.label

        # Parse the phone number
        parsed = self._parse_number(raw_number)
        if parsed is None:
            return [
                ModuleResult(
                    module_name=self.name,
                    source="phonenumbers",
                    finding_type="phone_analysis",
                    title=f"Unable to parse phone number: {raw_number}",
                    content="The phone number could not be parsed. Ensure it includes a country code.",
                    data={"number": raw_number, "error": "parse_failed"},
                    confidence=20,
                )
            ]

        # Analyze the number
        analysis = self._analyze_number(parsed, raw_number)
        results.append(analysis)

        # Risk assessment
        risk = self._assess_risk(analysis.data)
        results.append(risk)

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_number(self, raw: str) -> phonenumbers.PhoneNumber | None:
        """Attempt to parse a phone number string."""
        try:
            # Try parsing as-is (with country code)
            parsed = phonenumbers.parse(raw, None)
            if phonenumbers.is_valid_number(parsed):
                return parsed
        except phonenumbers.NumberParseException:
            pass

        # Fallback: try US if no country code
        try:
            parsed = phonenumbers.parse(raw, "US")
            if phonenumbers.is_valid_number(parsed):
                return parsed
        except phonenumbers.NumberParseException:
            pass

        # Last attempt: try with + prefix
        try:
            if not raw.startswith("+"):
                parsed = phonenumbers.parse(f"+{raw}", None)
                if phonenumbers.is_valid_number(parsed):
                    return parsed
        except phonenumbers.NumberParseException:
            pass

        return None

    def _analyze_number(
        self, parsed: phonenumbers.PhoneNumber, raw: str
    ) -> ModuleResult:
        """Analyze a parsed phone number for type, carrier, and VoIP indicators."""
        # Number type
        num_type = phonenumbers.number_type(parsed)
        type_map = {
            phonenumbers.PhoneNumberType.FIXED_LINE: "fixed_line",
            phonenumbers.PhoneNumberType.MOBILE: "mobile",
            phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed_line_or_mobile",
            phonenumbers.PhoneNumberType.TOLL_FREE: "toll_free",
            phonenumbers.PhoneNumberType.PREMIUM_RATE: "premium_rate",
            phonenumbers.PhoneNumberType.SHARED_COST: "shared_cost",
            phonenumbers.PhoneNumberType.VOIP: "voip",
            phonenumbers.PhoneNumberType.PERSONAL_NUMBER: "personal_number",
            phonenumbers.PhoneNumberType.PAGER: "pager",
            phonenumbers.PhoneNumberType.UAN: "uan",
            phonenumbers.PhoneNumberType.UNKNOWN: "unknown",
        }
        number_type_str = type_map.get(num_type, "unknown")
        is_voip_type = num_type == phonenumbers.PhoneNumberType.VOIP

        # Carrier
        carrier_name = carrier.name_for_number(parsed, "en") or "unknown"

        # Check carrier against known VoIP list
        is_voip_carrier = carrier_name.lower().strip() in VOIP_CARRIERS

        # Combined VoIP flag
        is_voip = is_voip_type or is_voip_carrier

        # Disposable risk (VoIP or known disposable carrier patterns)
        is_disposable_risk = is_voip

        # Country and region
        country = geocoder.description_for_number(parsed, "en") or "unknown"
        region_code = phonenumbers.region_code_for_number(parsed) or "unknown"

        # Timezone
        tz_list = list(timezone.time_zones_for_number(parsed))

        # Format
        formatted_e164 = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.E164
        )
        formatted_intl = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
        )

        return ModuleResult(
            module_name=self.name,
            source="phonenumbers",
            finding_type="phone_analysis",
            title=f"Phone analysis: {formatted_intl} ({number_type_str})",
            content=(
                f"Number {formatted_intl} is type={number_type_str}, "
                f"carrier={carrier_name}, voip={is_voip}, "
                f"disposable_risk={is_disposable_risk}, country={country}"
            ),
            data={
                "number": formatted_e164,
                "number_international": formatted_intl,
                "raw_input": raw,
                "type": number_type_str,
                "carrier": carrier_name,
                "is_voip": is_voip,
                "is_voip_type": is_voip_type,
                "is_voip_carrier": is_voip_carrier,
                "is_disposable_risk": is_disposable_risk,
                "country": country,
                "region": region_code,
                "timezones": tz_list,
                "valid": True,
            },
            confidence=80,
        )

    def _assess_risk(self, analysis_data: dict) -> ModuleResult:
        """Produce a risk assessment based on phone analysis data."""
        score = 0
        factors: list[str] = []

        if analysis_data.get("is_voip_type"):
            score += 40
            factors.append("voip_number_type (+40)")

        if analysis_data.get("is_voip_carrier"):
            score += 30
            factors.append("known_voip_carrier (+30)")

        if analysis_data.get("type") == "toll_free":
            score += 15
            factors.append("toll_free_number (+15)")

        if analysis_data.get("type") == "premium_rate":
            score += 10
            factors.append("premium_rate_number (+10)")

        if analysis_data.get("carrier") == "unknown":
            score += 10
            factors.append("unknown_carrier (+10)")

        # Clamp
        score = max(0, min(100, score))

        if score >= 60:
            risk_level = "high"
        elif score >= 30:
            risk_level = "medium"
        else:
            risk_level = "low"

        return ModuleResult(
            module_name=self.name,
            source="phonenumbers",
            finding_type="phone_risk",
            title=f"Phone risk: {score}/100 ({risk_level})",
            content=(
                f"Risk score {score}/100 — {risk_level}. "
                f"Factors: {', '.join(factors) if factors else 'none'}"
            ),
            data={
                "risk_score": score,
                "risk_level": risk_level,
                "factors": factors,
                "number": analysis_data.get("number", ""),
            },
            confidence=75,
        )
