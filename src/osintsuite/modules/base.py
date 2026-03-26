"""Abstract base class for all OSINT modules and shared data structures."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from osintsuite.db.models import Target

logger = logging.getLogger(__name__)


@dataclass
class ModuleResult:
    """A single finding produced by a module run."""

    module_name: str
    source: str
    finding_type: str
    title: str | None
    content: str | None
    data: dict[str, Any] = field(default_factory=dict)
    confidence: int = 50
    raw_response: str | None = None


class RateLimiter:
    """Simple token-bucket rate limiter for HTTP requests."""

    def __init__(self, rate_per_second: float = 2.0):
        self._rate = rate_per_second
        self._min_interval = 1.0 / rate_per_second
        self._last_request = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_request = time.monotonic()


class BaseModule(ABC):
    """Abstract base for all OSINT modules."""

    name: str = "base"
    description: str = "Base OSINT module"

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        rate_limiter: RateLimiter,
    ):
        self.http = http_client
        self.limiter = rate_limiter
        self.logger = logging.getLogger(f"osintsuite.modules.{self.name}")

    @abstractmethod
    async def run(self, target: Target) -> list[ModuleResult]:
        """Execute the module against a target. Returns a list of findings."""
        ...

    @abstractmethod
    def applicable_target_types(self) -> list[str]:
        """Return the target_type values this module can process."""
        ...

    async def fetch(self, url: str, **kwargs) -> httpx.Response | None:
        """Rate-limited HTTP GET with error handling."""
        await self.limiter.acquire()
        try:
            response = await self.http.get(url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as e:
            self.logger.warning(f"HTTP {e.response.status_code} for {url}")
            return None
        except httpx.RequestError as e:
            self.logger.warning(f"Request failed for {url}: {e}")
            return None
