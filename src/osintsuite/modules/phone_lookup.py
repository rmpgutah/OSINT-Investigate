"""Phone number lookup module — validates, formats, and identifies carrier info."""

from __future__ import annotations

from typing import TYPE_CHECKING

import phonenumbers
from phonenumbers import carrier, geocoder, timezone

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class PhoneLookupModule(BaseModule):
    name = "phone_lookup"
    description = "Phone number validation, formatting, carrier, and geolocation"

    def applicable_target_types(self) -> list[str]:
        return ["phone", "person"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        phone = target.phone or (target.label if target.target_type == "phone" else None)
        if not phone:
            return results

        try:
            parsed = phonenumbers.parse(phone, "US")
        except phonenumbers.NumberParseException as e:
            return [
                ModuleResult(
                    module_name=self.name,
                    source="phonenumbers",
                    finding_type="validation",
                    title=f"Invalid phone number: {phone}",
                    content=str(e),
                    data={"phone": phone, "valid": False, "error": str(e)},
                    confidence=95,
                )
            ]

        is_valid = phonenumbers.is_valid_number(parsed)
        number_type = phonenumbers.number_type(parsed)
        type_map = {
            phonenumbers.PhoneNumberType.MOBILE: "mobile",
            phonenumbers.PhoneNumberType.FIXED_LINE: "fixed_line",
            phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed_line_or_mobile",
            phonenumbers.PhoneNumberType.TOLL_FREE: "toll_free",
            phonenumbers.PhoneNumberType.VOIP: "voip",
        }

        carrier_name = carrier.name_for_number(parsed, "en") or "Unknown"
        location = geocoder.description_for_number(parsed, "en") or "Unknown"
        timezones = list(timezone.time_zones_for_number(parsed))
        formatted_e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        formatted_intl = phonenumbers.format_number(
            parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL
        )

        results.append(
            ModuleResult(
                module_name=self.name,
                source="phonenumbers",
                finding_type="phone_analysis",
                title=f"Phone analysis: {formatted_intl}",
                content=(
                    f"Number: {formatted_intl}\n"
                    f"Valid: {is_valid}\n"
                    f"Type: {type_map.get(number_type, 'unknown')}\n"
                    f"Carrier: {carrier_name}\n"
                    f"Location: {location}\n"
                    f"Timezones: {', '.join(timezones)}"
                ),
                data={
                    "phone": phone,
                    "formatted_e164": formatted_e164,
                    "formatted_international": formatted_intl,
                    "valid": is_valid,
                    "type": type_map.get(number_type, "unknown"),
                    "carrier": carrier_name,
                    "location": location,
                    "country_code": parsed.country_code,
                    "timezones": timezones,
                },
                confidence=90,
            )
        )

        return results
