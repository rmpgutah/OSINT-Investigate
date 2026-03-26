"""Metadata forensics module — HTTP headers, SSL certs, robots.txt, EXIF extraction."""

from __future__ import annotations

import io
import re
import ssl
import socket
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class MetadataForensicsModule(BaseModule):
    name = "metadata_forensics"
    description = "HTTP header analysis, SSL certificate extraction, robots.txt, and image EXIF data"

    def applicable_target_types(self) -> list[str]:
        return ["domain", "organization"]

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        domain = target.label

        if "://" in domain:
            domain = domain.split("://")[1].split("/")[0]

        base_url = f"https://{domain}"

        results.extend(await self._http_headers(domain, base_url))
        results.extend(await self._ssl_certificate(domain))
        results.extend(await self._robots_txt(domain, base_url))
        results.extend(await self._exif_scan(domain, base_url))

        return results

    async def _http_headers(self, domain: str, base_url: str) -> list[ModuleResult]:
        """Analyze HTTP response headers for server fingerprinting and security posture."""
        resp = await self.fetch(base_url)
        if not resp:
            return []

        headers = dict(resp.headers)
        results = []

        # Server fingerprint
        server = headers.get("server", "")
        powered_by = headers.get("x-powered-by", "")
        fingerprint_parts = [p for p in [server, powered_by] if p]

        if fingerprint_parts:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="http_headers",
                    finding_type="server_fingerprint",
                    title=f"Server: {' / '.join(fingerprint_parts)}",
                    content=f"Server: {server}\nX-Powered-By: {powered_by}",
                    data={
                        "domain": domain,
                        "server": server,
                        "x_powered_by": powered_by,
                    },
                    confidence=85,
                )
            )

        # Security headers analysis
        security_headers = {
            "strict-transport-security": "HSTS",
            "content-security-policy": "CSP",
            "x-content-type-options": "X-Content-Type-Options",
            "x-frame-options": "X-Frame-Options",
            "x-xss-protection": "X-XSS-Protection",
            "referrer-policy": "Referrer-Policy",
            "permissions-policy": "Permissions-Policy",
        }

        present = {}
        missing = []
        for header, label in security_headers.items():
            val = headers.get(header)
            if val:
                present[label] = val
            else:
                missing.append(label)

        results.append(
            ModuleResult(
                module_name=self.name,
                source="http_headers",
                finding_type="http_headers",
                title=f"Security headers for {domain}",
                content=f"Present: {len(present)}/{len(security_headers)} | Missing: {', '.join(missing) or 'none'}",
                data={
                    "domain": domain,
                    "present": present,
                    "missing": missing,
                    "all_headers": {k: v for k, v in headers.items() if not k.startswith(":")},
                },
                confidence=90,
            )
        )

        return results

    async def _ssl_certificate(self, domain: str) -> list[ModuleResult]:
        """Extract SSL/TLS certificate details."""
        try:
            ctx = ssl.create_default_context()
            with socket.create_connection((domain, 443), timeout=5) as sock:
                with ctx.wrap_socket(sock, server_hostname=domain) as ssock:
                    cert = ssock.getpeercert()

            if not cert:
                return []

            subject = dict(x[0] for x in cert.get("subject", ()))
            issuer = dict(x[0] for x in cert.get("issuer", ()))
            san = [
                entry[1]
                for entry in cert.get("subjectAltName", ())
                if entry[0] == "DNS"
            ]
            not_before = cert.get("notBefore", "")
            not_after = cert.get("notAfter", "")

            return [
                ModuleResult(
                    module_name=self.name,
                    source="ssl",
                    finding_type="ssl_certificate",
                    title=f"SSL cert: {subject.get('commonName', domain)}",
                    content=f"Issuer: {issuer.get('organizationName', 'Unknown')}\nExpires: {not_after}\nSANs: {len(san)} domains",
                    data={
                        "domain": domain,
                        "subject": subject,
                        "issuer": issuer,
                        "san": san[:50],  # Cap at 50 SANs
                        "not_before": not_before,
                        "not_after": not_after,
                        "serial_number": cert.get("serialNumber"),
                        "version": cert.get("version"),
                    },
                    confidence=95,
                )
            ]
        except Exception as e:
            self.logger.debug(f"SSL cert extraction failed for {domain}: {e}")
            return []

    async def _robots_txt(self, domain: str, base_url: str) -> list[ModuleResult]:
        """Parse robots.txt for disallowed paths (potential hidden content)."""
        resp = await self.fetch(f"{base_url}/robots.txt")
        if not resp or resp.status_code != 200:
            return []

        text = resp.text
        disallowed = []
        sitemaps = []

        for line in text.splitlines():
            line = line.strip()
            if line.lower().startswith("disallow:"):
                path = line.split(":", 1)[1].strip()
                if path and path != "/":
                    disallowed.append(path)
            elif line.lower().startswith("sitemap:"):
                url = line.split(":", 1)[1].strip()
                # Rejoin if the split broke the URL
                if not url.startswith("http"):
                    url = "https:" + url
                sitemaps.append(url)

        results = []
        if disallowed:
            results.append(
                ModuleResult(
                    module_name=self.name,
                    source="robots.txt",
                    finding_type="hidden_path",
                    title=f"robots.txt: {len(disallowed)} disallowed paths",
                    content="\n".join(disallowed[:30]),
                    data={
                        "domain": domain,
                        "disallowed": disallowed,
                        "sitemaps": sitemaps,
                        "total_disallowed": len(disallowed),
                    },
                    confidence=70,
                )
            )

        return results

    async def _exif_scan(self, domain: str, base_url: str) -> list[ModuleResult]:
        """Download first few images from target site and extract EXIF data."""
        # First, fetch the homepage and find image URLs
        resp = await self.fetch(base_url)
        if not resp:
            return []

        from bs4 import BeautifulSoup

        soup = BeautifulSoup(resp.text, "lxml")
        img_tags = soup.find_all("img", src=True)

        # Collect image URLs (max 5)
        image_urls = []
        for img in img_tags[:10]:
            src = img["src"]
            if src.startswith("data:"):
                continue
            if not src.startswith("http"):
                src = urljoin(base_url, src)
            if src.lower().endswith((".jpg", ".jpeg", ".png", ".tiff", ".tif")):
                image_urls.append(src)
            if len(image_urls) >= 5:
                break

        results = []
        for img_url in image_urls:
            exif_result = await self._extract_exif(img_url)
            if exif_result:
                results.append(exif_result)

        return results

    async def _extract_exif(self, image_url: str) -> ModuleResult | None:
        """Download an image and extract EXIF metadata."""
        try:
            from PIL import Image
            from PIL.ExifTags import TAGS

            resp = await self.fetch(image_url)
            if not resp or len(resp.content) > 10_000_000:  # Skip >10MB
                return None

            img = Image.open(io.BytesIO(resp.content))
            exif_raw = img._getexif()
            if not exif_raw:
                return None

            exif_data: dict[str, Any] = {}
            for tag_id, value in exif_raw.items():
                tag_name = TAGS.get(tag_id, str(tag_id))
                # Only include string-representable values
                try:
                    if isinstance(value, bytes):
                        continue  # Skip binary blobs
                    exif_data[tag_name] = str(value)
                except Exception:
                    continue

            if not exif_data:
                return None

            # Highlight interesting forensic fields
            interesting = {k: v for k, v in exif_data.items() if k in {
                "Make", "Model", "Software", "DateTime", "DateTimeOriginal",
                "GPSInfo", "Artist", "Copyright", "ImageDescription",
                "ExifImageWidth", "ExifImageHeight",
            }}

            return ModuleResult(
                module_name=self.name,
                source=image_url.split("/")[-1][:80],
                finding_type="exif_data",
                title=f"EXIF: {interesting.get('Make', '')} {interesting.get('Model', '')}".strip() or "EXIF metadata found",
                content="\n".join(f"{k}: {v}" for k, v in interesting.items()) if interesting else f"{len(exif_data)} EXIF tags found",
                data={
                    "image_url": image_url,
                    "exif": exif_data,
                    "interesting_fields": interesting,
                },
                confidence=80,
            )
        except Exception as e:
            self.logger.debug(f"EXIF extraction failed for {image_url}: {e}")
            return None
