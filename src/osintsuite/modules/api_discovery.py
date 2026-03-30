"""API discovery module — probes common API endpoints on a domain."""

from __future__ import annotations

from typing import TYPE_CHECKING

from osintsuite.modules.base import BaseModule, ModuleResult

if TYPE_CHECKING:
    from osintsuite.db.models import Target


class ApiDiscoveryModule(BaseModule):
    name = "api_discovery"
    description = "Discover exposed API endpoints, documentation, and configurations"

    PROBE_PATHS = [
        "/api",
        "/api/v1",
        "/api/v2",
        "/graphql",
        "/swagger.json",
        "/openapi.json",
        "/api-docs",
        "/.well-known/openid-configuration",
        "/wp-json/wp/v2",
        "/rest/api/latest",
        "/v1/health",
        "/actuator",
    ]

    DOC_PATHS = {"/swagger.json", "/openapi.json", "/api-docs"}

    def applicable_target_types(self) -> list[str]:
        return ["domain"]

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run(self, target: Target) -> list[ModuleResult]:
        results: list[ModuleResult] = []

        domain = target.label
        if not domain:
            self.logger.info("No domain available on target, skipping API discovery")
            return results

        # Ensure we have a proper base URL
        base_url = domain if domain.startswith("http") else f"https://{domain}"

        found_endpoints: list[dict] = []

        for path in self.PROBE_PATHS:
            url = f"{base_url}{path}"
            try:
                response = await self.fetch(url)
                if response and response.status_code in (200, 301, 302):
                    is_doc = path in self.DOC_PATHS
                    finding_type = "api_docs_found" if is_doc else "api_endpoint"
                    confidence = 80 if is_doc else 75

                    content_type = response.headers.get("content-type", "")
                    content_length = len(response.content) if response.content else 0

                    entry = {
                        "url": url,
                        "path": path,
                        "status_code": response.status_code,
                        "content_type": content_type,
                        "content_length": content_length,
                    }
                    found_endpoints.append(entry)

                    results.append(
                        ModuleResult(
                            module_name=self.name,
                            source="api_probe",
                            finding_type=finding_type,
                            title=f"{'API docs' if is_doc else 'API endpoint'} found: {path} (HTTP {response.status_code})",
                            content=(
                                f"Accessible endpoint at {url} returned HTTP {response.status_code}. "
                                f"Content-Type: {content_type}"
                            ),
                            data=entry,
                            confidence=confidence,
                        )
                    )
            except Exception as exc:
                self.logger.debug(f"Probe failed for {url}: {exc}")

        # Summary finding
        results.append(
            ModuleResult(
                module_name=self.name,
                source="api_discovery",
                finding_type="api_discovery_summary",
                title=f"API discovery summary for {domain}",
                content=(
                    f"Probed {len(self.PROBE_PATHS)} common API paths on {domain}. "
                    f"Found {len(found_endpoints)} accessible endpoint(s)."
                ),
                data={
                    "domain": domain,
                    "paths_probed": len(self.PROBE_PATHS),
                    "endpoints_found": len(found_endpoints),
                    "endpoints": found_endpoints,
                },
                confidence=65,
            )
        )

        return results
