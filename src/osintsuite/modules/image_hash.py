"""Extract and hash images from target websites for reverse image search."""

from __future__ import annotations

import hashlib
import re
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target

try:
    from bs4 import BeautifulSoup

    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False


class ImageHashModule(BaseModule):
    name = "image_hash"
    description = (
        "Extract and hash images from target websites for reverse image search"
    )

    def applicable_target_types(self) -> list[str]:
        return ["domain"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []
        domain = target.label

        # Ensure we have a URL to fetch
        base_url = domain if domain.startswith("http") else f"https://{domain}"

        # Fetch homepage
        resp = await self.fetch(base_url)
        if resp is None:
            return [
                ModuleResult(
                    module_name=self.name,
                    source=domain,
                    finding_type="image_hash_summary",
                    title=f"Image hash: could not fetch {domain}",
                    content="Failed to retrieve the homepage.",
                    data={"domain": domain, "error": "fetch_failed", "total_images": 0},
                    confidence=20,
                )
            ]

        html = resp.text

        # Extract image URLs
        if _HAS_BS4:
            image_entries = self._extract_images_bs4(html, base_url)
        else:
            image_entries = self._extract_images_regex(html, base_url)

        # Process each image (max 10)
        processed = 0
        for entry in image_entries[:10]:
            img_result = await self._process_image(entry, domain)
            if img_result is not None:
                results.append(img_result)
                processed += 1

        # Summary
        results.append(
            ModuleResult(
                module_name=self.name,
                source=domain,
                finding_type="image_hash_summary",
                title=f"Image hash summary for {domain} ({processed} images)",
                content=f"Extracted and hashed {processed} images from {domain}.",
                data={
                    "domain": domain,
                    "total_images": processed,
                    "images_found_in_html": len(image_entries),
                },
                confidence=70,
            )
        )

        return results

    # ------------------------------------------------------------------
    # Image extraction
    # ------------------------------------------------------------------

    def _extract_images_bs4(
        self, html: str, base_url: str
    ) -> list[dict[str, str]]:
        """Extract image URLs and alt text using BeautifulSoup."""
        soup = BeautifulSoup(html, "html.parser")
        images: list[dict[str, str]] = []

        for img in soup.find_all("img"):
            src = img.get("src", "")
            if not src:
                # Try data-src, srcset, or data-lazy-src
                src = img.get("data-src", "") or img.get("data-lazy-src", "")
                if not src:
                    srcset = img.get("srcset", "")
                    if srcset:
                        src = srcset.split(",")[0].strip().split(" ")[0]

            if not src:
                continue

            # Skip data URIs and tiny icons
            if src.startswith("data:"):
                continue

            # Resolve relative URLs
            absolute_url = urljoin(base_url, src)

            alt_text = img.get("alt", "")
            width = img.get("width", "")
            height = img.get("height", "")

            images.append({
                "url": absolute_url,
                "alt_text": alt_text,
                "width": str(width),
                "height": str(height),
            })

        return images

    def _extract_images_regex(
        self, html: str, base_url: str
    ) -> list[dict[str, str]]:
        """Fallback regex-based image extraction."""
        images: list[dict[str, str]] = []
        pattern = re.compile(
            r'<img[^>]+src=["\']([^"\']+)["\']([^>]*)>',
            re.IGNORECASE | re.DOTALL,
        )

        for match in pattern.finditer(html):
            src = match.group(1).strip()
            attrs = match.group(2)

            if not src or src.startswith("data:"):
                continue

            absolute_url = urljoin(base_url, src)

            # Try to extract alt text
            alt_match = re.search(r'alt=["\']([^"\']*)["\']', attrs, re.IGNORECASE)
            alt_text = alt_match.group(1) if alt_match else ""

            images.append({
                "url": absolute_url,
                "alt_text": alt_text,
                "width": "",
                "height": "",
            })

        return images

    # ------------------------------------------------------------------
    # Image processing and hashing
    # ------------------------------------------------------------------

    async def _process_image(
        self, entry: dict[str, str], domain: str
    ) -> ModuleResult | None:
        """Fetch a single image, compute hashes, and return a finding."""
        url = entry["url"]

        try:
            resp = await self.fetch(url)
        except Exception as exc:
            self.logger.debug("Failed to fetch image %s: %s", url, exc)
            return None

        if resp is None:
            return None

        content = resp.content
        if not content:
            return None

        content_type = resp.headers.get("content-type", "unknown")
        size_bytes = len(content)

        # Skip very small images (likely tracking pixels)
        if size_bytes < 100:
            return None

        # Compute hashes
        md5_hash = hashlib.md5(content).hexdigest()
        sha256_hash = hashlib.sha256(content).hexdigest()

        return ModuleResult(
            module_name=self.name,
            source=domain,
            finding_type="image_found",
            title=f"Image: {url.split('/')[-1][:80]}",
            content=f"MD5={md5_hash}, SHA256={sha256_hash[:16]}..., size={size_bytes}B",
            data={
                "url": url,
                "alt_text": entry.get("alt_text", ""),
                "md5": md5_hash,
                "sha256": sha256_hash,
                "content_type": content_type,
                "size_bytes": size_bytes,
                "html_width": entry.get("width", ""),
                "html_height": entry.get("height", ""),
            },
            confidence=80,
        )
