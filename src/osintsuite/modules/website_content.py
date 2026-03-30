"""Website content analysis module — meta tags, emails, phones, robots.txt, sitemap."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class WebsiteContentModule(BaseModule):
    name = "website_content"
    description = "Website content analysis — meta tags, contact info, robots.txt, sitemap"

    MAX_RESULTS = 30

    # Regex patterns
    EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
    PHONE_RE = re.compile(
        r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"
    )

    def applicable_target_types(self) -> list[str]:
        return ["domain"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        domain = target.label or getattr(target, "domain", None) or ""
        if not domain:
            self.logger.info("No domain available on target, skipping website content")
            return results

        # Normalize domain — strip protocol if present
        domain = domain.strip()
        if "://" in domain:
            domain = domain.split("://", 1)[1]
        domain = domain.rstrip("/")

        base_url = f"https://{domain}"

        # 1. Fetch and analyze homepage
        results.extend(await self._analyze_homepage(base_url, domain))

        # 2. Check robots.txt
        results.extend(await self._check_robots(base_url, domain))

        # 3. Check sitemap.xml
        results.extend(await self._check_sitemap(base_url, domain))

        capped = results[: self.MAX_RESULTS]

        # Summary finding
        email_count = sum(1 for r in capped if r.finding_type == "extracted_email")
        phone_count = sum(1 for r in capped if r.finding_type == "extracted_phone")

        capped.append(
            ModuleResult(
                module_name=self.name,
                source="website_content",
                finding_type="website_content_summary",
                title=f"Website content summary for {domain}",
                content=(
                    f"Analyzed {domain}: found {email_count} email(s), "
                    f"{phone_count} phone number(s), and {len(capped)} total finding(s)."
                ),
                data={
                    "domain": domain,
                    "total_results": len(capped),
                    "email_count": email_count,
                    "phone_count": phone_count,
                },
                confidence=65,
            )
        )

        return capped

    # ------------------------------------------------------------------
    # Homepage analysis
    # ------------------------------------------------------------------

    async def _analyze_homepage(
        self, base_url: str, domain: str
    ) -> list[ModuleResult]:
        """Fetch the homepage and extract meta tags, emails, and phone numbers."""
        results: list[ModuleResult] = []

        try:
            response = await self.fetch(base_url)
        except Exception as exc:
            self.logger.warning(f"Homepage fetch failed for {base_url}: {exc}")
            return results

        if not response:
            return results

        html = response.text

        # --- Meta tags ---
        meta_data = self._extract_meta(html)
        if meta_data:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="homepage",
                    finding_type="website_meta",
                    title=f"Website metadata for {domain}",
                    content=(
                        f"Title: {meta_data.get('title', 'N/A')}. "
                        f"Description: {meta_data.get('description', 'N/A')[:200]}."
                    ),
                    data={
                        "domain": domain,
                        "url": base_url,
                        **meta_data,
                        "source": "homepage",
                    },
                    confidence=80,
                )
            )

        # --- Emails ---
        emails = set(self.EMAIL_RE.findall(html))
        # Filter out common false positives
        ignored_extensions = {".png", ".jpg", ".gif", ".css", ".js", ".svg", ".woff"}
        emails = {
            e
            for e in emails
            if not any(e.lower().endswith(ext) for ext in ignored_extensions)
        }

        for email in sorted(emails)[:10]:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="homepage",
                    finding_type="extracted_email",
                    title=f"Email found on {domain}: {email}",
                    content=f"Email address {email} extracted from {domain} homepage.",
                    data={
                        "email": email,
                        "domain": domain,
                        "url": base_url,
                        "source": "homepage_extraction",
                    },
                    confidence=75,
                )
            )

        # --- Phone numbers ---
        phones = set(self.PHONE_RE.findall(html))
        for phone in sorted(phones)[:10]:
            phone_clean = phone.strip()
            if len(phone_clean) < 7:
                continue
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="homepage",
                    finding_type="extracted_phone",
                    title=f"Phone found on {domain}: {phone_clean}",
                    content=f"Phone number {phone_clean} extracted from {domain} homepage.",
                    data={
                        "phone": phone_clean,
                        "domain": domain,
                        "url": base_url,
                        "source": "homepage_extraction",
                    },
                    confidence=75,
                )
            )

        self.logger.info(
            f"Homepage analysis: {len(emails)} email(s), {len(phones)} phone(s) for {domain}"
        )
        return results

    # ------------------------------------------------------------------
    # robots.txt analysis
    # ------------------------------------------------------------------

    async def _check_robots(
        self, base_url: str, domain: str
    ) -> list[ModuleResult]:
        """Fetch and analyze robots.txt for interesting disallowed paths."""
        results: list[ModuleResult] = []

        robots_url = f"{base_url}/robots.txt"

        try:
            response = await self.fetch(robots_url)
        except Exception as exc:
            self.logger.warning(f"robots.txt fetch failed for {domain}: {exc}")
            return results

        if not response:
            return results

        text = response.text
        if not text or "<html" in text.lower()[:200]:
            # Got an HTML page instead of robots.txt
            return results

        # Parse disallowed paths
        disallowed: list[str] = []
        interesting_keywords = [
            "admin", "api", "login", "dashboard", "panel", "config",
            "internal", "private", "staging", "debug", "backup",
            "wp-admin", "phpmyadmin", "cpanel",
        ]

        for line in text.splitlines():
            line = line.strip()
            if line.lower().startswith("disallow:"):
                path = line.split(":", 1)[1].strip()
                if path and path != "/":
                    disallowed.append(path)

        # Filter for interesting paths
        interesting_paths = [
            p
            for p in disallowed
            if any(kw in p.lower() for kw in interesting_keywords)
        ]

        if disallowed:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="robots_txt",
                    finding_type="robots_entry",
                    title=f"robots.txt analysis for {domain}",
                    content=(
                        f"Found {len(disallowed)} disallowed path(s), "
                        f"{len(interesting_paths)} potentially interesting. "
                        f"Notable: {', '.join(interesting_paths[:5]) or 'none'}."
                    ),
                    data={
                        "domain": domain,
                        "url": robots_url,
                        "total_disallowed": len(disallowed),
                        "interesting_paths": interesting_paths[:10],
                        "all_disallowed": disallowed[:20],
                        "source": "robots_txt",
                    },
                    confidence=70,
                )
            )

        self.logger.info(
            f"robots.txt: {len(disallowed)} disallowed path(s) for {domain}"
        )
        return results

    # ------------------------------------------------------------------
    # sitemap.xml analysis
    # ------------------------------------------------------------------

    async def _check_sitemap(
        self, base_url: str, domain: str
    ) -> list[ModuleResult]:
        """Fetch and analyze sitemap.xml for page count and key URLs."""
        results: list[ModuleResult] = []

        sitemap_url = f"{base_url}/sitemap.xml"

        try:
            response = await self.fetch(sitemap_url)
        except Exception as exc:
            self.logger.warning(f"sitemap.xml fetch failed for {domain}: {exc}")
            return results

        if not response:
            return results

        text = response.text
        if not text or "<urlset" not in text.lower()[:500] and "<sitemapindex" not in text.lower()[:500]:
            return results

        # Extract URLs from sitemap
        url_pattern = re.compile(r"<loc>\s*(https?://[^<]+?)\s*</loc>", re.IGNORECASE)
        urls_found = url_pattern.findall(text)

        # Extract key URLs (interesting paths)
        key_urls = [
            u
            for u in urls_found[:100]
            if any(
                kw in u.lower()
                for kw in ["blog", "about", "contact", "team", "product", "service", "pricing"]
            )
        ]

        if urls_found:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="sitemap_xml",
                    finding_type="sitemap_info",
                    title=f"Sitemap analysis for {domain}",
                    content=(
                        f"Sitemap contains {len(urls_found)} URL(s). "
                        f"Key pages found: {len(key_urls)}."
                    ),
                    data={
                        "domain": domain,
                        "url": sitemap_url,
                        "total_urls": len(urls_found),
                        "key_urls": key_urls[:10],
                        "sample_urls": urls_found[:10],
                        "source": "sitemap_xml",
                    },
                    confidence=70,
                )
            )

        self.logger.info(
            f"sitemap.xml: {len(urls_found)} URL(s) for {domain}"
        )
        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_meta(html: str) -> dict[str, Any]:
        """Extract title, meta description, keywords, and OG tags from HTML."""
        meta: dict[str, Any] = {}

        # Title
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if title_match:
            meta["title"] = title_match.group(1).strip()

        # Meta description
        desc_match = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
            html,
            re.IGNORECASE,
        )
        if not desc_match:
            desc_match = re.search(
                r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']description["\']',
                html,
                re.IGNORECASE,
            )
        if desc_match:
            meta["description"] = desc_match.group(1).strip()

        # Meta keywords
        kw_match = re.search(
            r'<meta[^>]+name=["\']keywords["\'][^>]+content=["\'](.*?)["\']',
            html,
            re.IGNORECASE,
        )
        if not kw_match:
            kw_match = re.search(
                r'<meta[^>]+content=["\'](.*?)["\'][^>]+name=["\']keywords["\']',
                html,
                re.IGNORECASE,
            )
        if kw_match:
            meta["keywords"] = kw_match.group(1).strip()

        # OG tags
        og_tags: dict[str, str] = {}
        og_pattern = re.compile(
            r'<meta[^>]+property=["\']og:(\w+)["\'][^>]+content=["\'](.*?)["\']',
            re.IGNORECASE,
        )
        for og_match in og_pattern.finditer(html):
            og_tags[og_match.group(1)] = og_match.group(2).strip()

        # Also try reversed attribute order
        og_pattern_rev = re.compile(
            r'<meta[^>]+content=["\'](.*?)["\'][^>]+property=["\']og:(\w+)["\']',
            re.IGNORECASE,
        )
        for og_match in og_pattern_rev.finditer(html):
            og_tags.setdefault(og_match.group(2), og_match.group(1).strip())

        if og_tags:
            meta["og_tags"] = og_tags

        return meta
