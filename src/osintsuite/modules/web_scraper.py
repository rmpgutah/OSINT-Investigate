"""General-purpose web scraper module.

Replaces the original bs4_scraper.py tutorial and scrapywebspider.py with a proper
async module that fetches pages, extracts structured content, and returns findings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class WebScraperModule(BaseModule):
    name = "web_scraper"
    description = "Scrape web pages for content, links, and metadata"

    def applicable_target_types(self) -> list[str]:
        return ["domain", "person", "organization"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        urls = self._get_urls(target)

        for url in urls:
            page_results = await self._scrape_page(url)
            results.extend(page_results)

        return results

    def _get_urls(self, target: Target) -> list[str]:
        """Extract URLs to scrape from target metadata or construct from domain."""
        urls = target.metadata_.get("urls", [])
        if not urls and target.target_type == "domain":
            domain = target.label
            if not domain.startswith("http"):
                domain = f"https://{domain}"
            urls = [domain]
        return urls

    async def _scrape_page(self, url: str) -> list[ModuleResult]:
        """Fetch and parse a single page."""
        response = await self.fetch(url)
        if not response:
            return []

        soup = BeautifulSoup(response.text, "lxml")
        results = []

        # Extract page metadata
        title = soup.title.string.strip() if soup.title and soup.title.string else url
        meta_desc = ""
        meta_tag = soup.find("meta", attrs={"name": "description"})
        if meta_tag and meta_tag.get("content"):
            meta_desc = meta_tag["content"]

        results.append(
            ModuleResult(
                module_name=self.name,
                source=urlparse(url).netloc,
                finding_type="scraped_page",
                title=title,
                content=meta_desc or soup.get_text(separator=" ", strip=True)[:2000],
                data={
                    "url": url,
                    "title": title,
                    "meta_description": meta_desc,
                    "status_code": response.status_code,
                },
                confidence=70,
            )
        )

        # Extract all links
        links = set()
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]
            if isinstance(href, str):
                absolute = urljoin(url, href)
                if absolute.startswith("http"):
                    links.add(absolute)

        if links:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source=urlparse(url).netloc,
                    finding_type="link_list",
                    title=f"Links found on {title}",
                    content="\n".join(sorted(links)[:100]),
                    data={"url": url, "link_count": len(links), "links": sorted(links)[:100]},
                    confidence=80,
                )
            )

        # Extract emails from page text
        import re

        page_text = soup.get_text()
        emails = set(re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", page_text))
        for email in emails:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source=urlparse(url).netloc,
                    finding_type="email",
                    title=f"Email found: {email}",
                    content=email,
                    data={"url": url, "email": email},
                    confidence=75,
                )
            )

        self.logger.info(f"Scraped {url}: {len(results)} findings")
        return results
