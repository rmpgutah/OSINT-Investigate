"""Person search module — searches public directories and search engines for PII.

This is the secure, async replacement for the original osintpiibarf2csv bash script.
All HTTP requests go through httpx with proper headers and rate limiting.
No shell commands, no string interpolation into URLs without encoding.
"""

from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class PersonSearchModule(BaseModule):
    name = "person_search"
    description = "Search public directories and engines for person information"

    def applicable_target_types(self) -> list[str]:
        return ["person"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        if not target.full_name:
            self.logger.info("No full_name on target, skipping person search")
            return results

        name = target.full_name
        city = target.city or ""
        state = target.state or ""
        location = f"{city} {state}".strip()

        # Run searches
        results.extend(await self._search_google(name, location))
        results.extend(await self._search_whitepages(name, city, state))
        results.extend(await self._search_familysearch(name, location, target.date_of_birth))

        return results

    async def _search_google(self, name: str, location: str) -> list[ModuleResult]:
        """Search Google for a person's name + location."""
        query = f"{name} {location}".strip()
        encoded = urllib.parse.quote_plus(query)
        url = f"https://www.google.com/search?q={encoded}"

        response = await self.fetch(url)
        if not response:
            return []

        soup = BeautifulSoup(response.text, "lxml")
        results = []

        for link_tag in soup.select("a[href]"):
            href = link_tag.get("href", "")
            if isinstance(href, str) and href.startswith("/url?q="):
                clean_url = href.split("/url?q=")[1].split("&")[0]
                clean_url = urllib.parse.unquote(clean_url)
                if clean_url.startswith("http"):
                    text = link_tag.get_text(strip=True)
                    results.append(
                        ModuleResult(
                            module_name=self.name,
                            source="google",
                            finding_type="url",
                            title=text or clean_url,
                            content=clean_url,
                            data={"url": clean_url, "query": query},
                            confidence=30,
                            raw_response=None,
                        )
                    )

        self.logger.info(f"Google search found {len(results)} results for '{query}'")
        return results

    async def _search_whitepages(
        self, name: str, city: str, state: str
    ) -> list[ModuleResult]:
        """Search Whitepages for a person."""
        name_slug = name.lower().replace(" ", "-")
        location_slug = f"{city}-{state}".lower().replace(" ", "-") if city else ""
        url = f"https://www.whitepages.com/name/{urllib.parse.quote(name_slug)}"
        if location_slug:
            url += f"/{urllib.parse.quote(location_slug)}"

        response = await self.fetch(url)
        if not response:
            return []

        soup = BeautifulSoup(response.text, "lxml")
        results = []

        for link_tag in soup.select("a[href*='/name/']"):
            href = link_tag.get("href", "")
            if isinstance(href, str) and "/name/" in href:
                full_url = f"https://www.whitepages.com{href}" if href.startswith("/") else href
                text = link_tag.get_text(strip=True)
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="whitepages",
                        finding_type="profile",
                        title=text or "Whitepages Profile",
                        content=full_url,
                        data={"url": full_url, "name": name},
                        confidence=40,
                    )
                )

        self.logger.info(f"Whitepages search found {len(results)} results for '{name}'")
        return results

    async def _search_familysearch(
        self, name: str, location: str, dob=None
    ) -> list[ModuleResult]:
        """Search FamilySearch for genealogy/DOB records."""
        parts = name.split(maxsplit=1)
        given = urllib.parse.quote_plus(parts[0]) if parts else ""
        surname = urllib.parse.quote_plus(parts[1]) if len(parts) > 1 else ""

        params = [f"+givenname:{given}", f"+surname:{surname}"]
        if location:
            params.append(f"+birth_place:{urllib.parse.quote_plus(location)}")
        if dob:
            params.append(f"+birth_year:{dob.year}")

        query_str = " ".join(params)
        url = f"https://www.familysearch.org/search/results?count=20&query={urllib.parse.quote(query_str)}"

        response = await self.fetch(url)
        if not response:
            return []

        soup = BeautifulSoup(response.text, "lxml")
        results = []

        for link_tag in soup.select("a[href*='/tree/']"):
            href = link_tag.get("href", "")
            if isinstance(href, str):
                full_url = (
                    f"https://www.familysearch.org{href}" if href.startswith("/") else href
                )
                results.append(
                    ModuleResult(
                        module_name=self.name,
                        source="familysearch",
                        finding_type="record",
                        title=f"FamilySearch record for {name}",
                        content=full_url,
                        data={"url": full_url, "name": name},
                        confidence=35,
                    )
                )

        self.logger.info(f"FamilySearch found {len(results)} results for '{name}'")
        return results
