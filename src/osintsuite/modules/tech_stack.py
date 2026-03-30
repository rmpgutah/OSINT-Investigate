"""Technology stack detection module — CMS, frameworks, servers, analytics, CDNs."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


# Pattern definitions: (technology_name, category, regex_or_check, evidence_description)
HEADER_PATTERNS = [
    ("Apache", "web_server", "server", r"(?i)apache"),
    ("Nginx", "web_server", "server", r"(?i)nginx"),
    ("IIS", "web_server", "server", r"(?i)microsoft-iis"),
    ("LiteSpeed", "web_server", "server", r"(?i)litespeed"),
    ("Caddy", "web_server", "server", r"(?i)caddy"),
    ("PHP", "language", "x-powered-by", r"(?i)php"),
    ("ASP.NET", "framework", "x-powered-by", r"(?i)asp\.net"),
    ("Express", "framework", "x-powered-by", r"(?i)express"),
    ("Cloudflare", "cdn", "cf-ray", r".+"),
    ("Cloudflare", "cdn", "server", r"(?i)cloudflare"),
    ("AWS CloudFront", "cdn", "x-amz-cf-id", r".+"),
    ("AWS CloudFront", "cdn", "via", r"(?i)cloudfront"),
    ("Fastly", "cdn", "x-served-by", r"(?i)cache"),
    ("Fastly", "cdn", "via", r"(?i)varnish"),
    ("Akamai", "cdn", "x-akamai-transformed", r".+"),
    ("Varnish", "caching", "via", r"(?i)varnish"),
    ("Django", "framework", "x-frame-options", None),  # heuristic only
]

HTML_PATTERNS = [
    ("WordPress", "cms", r'(?:wp-content|wp-includes|/wp-admin)', "HTML source references"),
    ("Joomla", "cms", r'(?:/administrator|/components/com_|Joomla!)', "HTML source references"),
    ("Drupal", "cms", r'(?:drupal\.js|/sites/default/|Drupal\.settings)', "HTML source references"),
    ("Shopify", "ecommerce", r'(?:cdn\.shopify\.com|Shopify\.theme)', "HTML source references"),
    ("Squarespace", "cms", r'(?:squarespace\.com|static\.squarespace)', "HTML source references"),
    ("Wix", "cms", r'(?:wix\.com|parastorage\.com)', "HTML source references"),
    ("React", "js_framework", r'(?:react\.production|react-dom|__NEXT_DATA__|_reactRoot)', "Script/DOM patterns"),
    ("Next.js", "js_framework", r'(?:__NEXT_DATA__|_next/static|next/dist)', "Script/DOM patterns"),
    ("Angular", "js_framework", r'(?:ng-version|angular\.(?:min\.)?js|ng-app)', "Script/DOM patterns"),
    ("Vue.js", "js_framework", r'(?:vue\.(?:min\.)?js|v-cloak|__vue__|nuxt)', "Script/DOM patterns"),
    ("Nuxt.js", "js_framework", r'(?:__NUXT__|_nuxt/)', "Script/DOM patterns"),
    ("jQuery", "js_library", r'(?:jquery[.-][\d.]+\.(?:min\.)?js|jquery\.min\.js)', "Script patterns"),
    ("Bootstrap", "css_framework", r'(?:bootstrap[.-][\d.]*\.(?:min\.)?(?:css|js)|getbootstrap)', "Asset references"),
    ("Tailwind CSS", "css_framework", r'(?:tailwindcss|tailwind\.min\.css)', "Asset references"),
    ("Google Analytics", "analytics", r'(?:google-analytics\.com|googletagmanager\.com|gtag\(|UA-\d+|G-[A-Z0-9]+)', "Script patterns"),
    ("Facebook Pixel", "analytics", r'(?:connect\.facebook\.net|fbq\(|fbevents\.js)', "Script patterns"),
    ("Hotjar", "analytics", r'(?:hotjar\.com|hj\(|_hjSettings)', "Script patterns"),
    ("Google Tag Manager", "analytics", r'(?:googletagmanager\.com/gtm\.js)', "Script patterns"),
    ("Matomo", "analytics", r'(?:matomo\.js|piwik\.js|_paq\.push)', "Script patterns"),
    ("Google Fonts", "font_service", r'(?:fonts\.googleapis\.com|fonts\.gstatic\.com)', "Asset references"),
    ("Font Awesome", "icon_library", r'(?:fontawesome|font-awesome)', "Asset references"),
    ("reCAPTCHA", "security", r'(?:recaptcha|google\.com/recaptcha)', "Script patterns"),
    ("hCaptcha", "security", r'(?:hcaptcha\.com)', "Script patterns"),
]

META_GENERATOR_MAP = {
    "wordpress": ("WordPress", "cms"),
    "joomla": ("Joomla", "cms"),
    "drupal": ("Drupal", "cms"),
    "blogger": ("Blogger", "cms"),
    "ghost": ("Ghost", "cms"),
    "hugo": ("Hugo", "static_site_gen"),
    "jekyll": ("Jekyll", "static_site_gen"),
    "gatsby": ("Gatsby", "static_site_gen"),
    "weebly": ("Weebly", "cms"),
    "typo3": ("TYPO3", "cms"),
    "magento": ("Magento", "ecommerce"),
    "prestashop": ("PrestaShop", "ecommerce"),
}


class TechStackModule(BaseModule):
    name = "tech_stack"
    description = "Website technology stack detection (CMS, frameworks, servers, analytics)"

    def applicable_target_types(self) -> list[str]:
        return ["domain"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        domain = target.label

        # Strip protocol if present
        if "://" in domain:
            domain = domain.split("://")[1].split("/")[0]

        url = f"https://{domain}"
        response = await self.fetch(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36",
            },
            follow_redirects=True,
        )

        if not response:
            # Try HTTP fallback
            url = f"http://{domain}"
            response = await self.fetch(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36",
                },
                follow_redirects=True,
            )

        if not response:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="tech_stack",
                    finding_type="error",
                    title=f"Could not reach {domain}",
                    content=f"Failed to fetch {domain} over HTTPS and HTTP.",
                    data={"domain": domain, "error": "unreachable"},
                    confidence=0,
                )
            )
            return results

        detected: dict[str, dict] = {}  # tech_name -> {category, evidence, confidence}

        # Analyze HTTP headers
        self._analyze_headers(response, detected)

        # Analyze HTML body
        html = response.text
        self._analyze_html(html, detected)

        # Analyze meta generator tag
        self._analyze_meta_generator(html, detected)

        # Check for CMS-specific paths
        await self._check_cms_paths(domain, detected)

        # Produce results
        for tech_name, info in sorted(detected.items()):
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="tech_stack",
                    finding_type="tech_detected",
                    title=f"Technology: {tech_name} ({info['category']})",
                    content=f"Detected {tech_name} [{info['category']}] via {info['evidence']}",
                    data={
                        "technology": tech_name,
                        "category": info["category"],
                        "evidence": info["evidence"],
                        "confidence": info["confidence"],
                        "domain": domain,
                    },
                    confidence=info["confidence"],
                )
            )

        # Summary
        categories = set(v["category"] for v in detected.values())
        results.append(
            ModuleResult(
                module_name=self.name,
                source="tech_stack",
                finding_type="tech_stack_summary",
                title=f"Tech stack summary for {domain}",
                content=(
                    f"Detected {len(detected)} technologies across "
                    f"{len(categories)} categories: {', '.join(sorted(categories))}"
                ),
                data={
                    "domain": domain,
                    "total_technologies": len(detected),
                    "categories": sorted(categories),
                    "technologies": list(detected.keys()),
                },
                confidence=75,
            )
        )

        return results

    def _analyze_headers(self, response, detected: dict) -> None:
        """Check HTTP response headers against known patterns."""
        headers = {k.lower(): v for k, v in response.headers.items()}

        for tech, category, header_name, pattern in HEADER_PATTERNS:
            if pattern is None:
                continue
            value = headers.get(header_name, "")
            if value and re.search(pattern, value):
                if tech not in detected:
                    detected[tech] = {
                        "category": category,
                        "evidence": f"HTTP header '{header_name}: {value[:100]}'",
                        "confidence": 85,
                    }

        # X-Generator header
        generator = headers.get("x-generator", "")
        if generator:
            detected.setdefault(generator.split("/")[0].strip(), {
                "category": "generator",
                "evidence": f"X-Generator header: {generator[:100]}",
                "confidence": 90,
            })

    def _analyze_html(self, html: str, detected: dict) -> None:
        """Scan HTML source for technology fingerprints."""
        for tech, category, pattern, evidence_desc in HTML_PATTERNS:
            if re.search(pattern, html, re.IGNORECASE):
                if tech not in detected:
                    detected[tech] = {
                        "category": category,
                        "evidence": evidence_desc,
                        "confidence": 70,
                    }

    def _analyze_meta_generator(self, html: str, detected: dict) -> None:
        """Extract and identify the <meta name="generator"> tag."""
        match = re.search(
            r'<meta\s+name=["\']generator["\']\s+content=["\']([^"\']+)',
            html,
            re.IGNORECASE,
        )
        if not match:
            # Also try reversed attribute order
            match = re.search(
                r'<meta\s+content=["\']([^"\']+)["\']\s+name=["\']generator',
                html,
                re.IGNORECASE,
            )
        if match:
            generator = match.group(1).strip()
            gen_lower = generator.lower()
            for key, (tech, category) in META_GENERATOR_MAP.items():
                if key in gen_lower:
                    detected[tech] = {
                        "category": category,
                        "evidence": f"Meta generator: {generator}",
                        "confidence": 95,
                    }
                    return
            # Unknown generator, still record it
            detected.setdefault(generator.split()[0], {
                "category": "generator",
                "evidence": f"Meta generator: {generator}",
                "confidence": 80,
            })

    async def _check_cms_paths(self, domain: str, detected: dict) -> None:
        """Probe well-known CMS paths for existence."""
        checks = [
            ("/wp-login.php", "WordPress", "cms"),
            ("/wp-admin/", "WordPress", "cms"),
            ("/administrator/", "Joomla", "cms"),
            ("/user/login", "Drupal", "cms"),
            ("/ghost/", "Ghost", "cms"),
        ]

        for path, tech, category in checks:
            if tech in detected:
                continue
            url = f"https://{domain}{path}"
            try:
                await self.limiter.acquire()
                resp = await self.http.head(
                    url,
                    follow_redirects=True,
                    timeout=10.0,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36",
                    },
                )
                if resp.status_code == 200:
                    detected[tech] = {
                        "category": category,
                        "evidence": f"CMS path exists: {path}",
                        "confidence": 80,
                    }
            except Exception:
                continue
