"""Async HTTP and browser-backed harvesting."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
import httpx

from .schemas import CrawlTarget


@dataclass(frozen=True, slots=True)
class HarvestResult:
    """Result of one fetch attempt sequence."""

    target: CrawlTarget
    html: str | None
    status_code: int | None
    attempts: int
    latency_ms: float
    error: str | None = None


class AsyncHarvestEngine:
    """Fetch crawl targets with bounded concurrency and retry control."""

    def __init__(
        self,
        *,
        max_concurrency: int = 8,
        per_domain_delay: float = 0.0,
        timeout: float = 20.0,
        backoff_factor: float = 0.25,
        client: httpx.AsyncClient | None = None,
        user_agent: str = "dataharvest-qa/1.0",
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")
        if per_domain_delay < 0 or timeout <= 0 or backoff_factor < 0:
            raise ValueError("delay, timeout, and backoff must be non-negative and timeout must be positive")
        self.max_concurrency = max_concurrency
        self.per_domain_delay = per_domain_delay
        self.timeout = timeout
        self.backoff_factor = backoff_factor
        self._client = client
        self._owns_client = client is None
        self._user_agent = user_agent
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._domain_locks: dict[str, asyncio.Lock] = {}
        self._next_domain_time: dict[str, float] = {}
        self._playwright: Any = None
        self._browser: Any = None

    async def __aenter__(self) -> "AsyncHarvestEngine":
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.close()

    async def start(self) -> None:
        """Open the shared HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": self._user_agent},
            )

    async def close(self) -> None:
        """Close HTTP and browser resources."""
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def fetch(self, target: CrawlTarget) -> HarvestResult:
        """Fetch one target, retrying transient failures."""
        started = time.perf_counter()
        attempts = 0
        max_attempts = target.max_retries + 1
        last_error: Exception | None = None
        async with self._semaphore:
            while attempts < max_attempts:
                attempts += 1
                try:
                    await self.start()
                    if target.render_js:
                        html, status_code = await self._fetch_dynamic(target)
                    else:
                        html, status_code = await self._fetch_static(target)
                    return HarvestResult(target, html, status_code, attempts, self._latency(started))
                except Exception as exc:
                    last_error = exc
                    if attempts >= max_attempts or not self._retryable(exc):
                        break
                    await asyncio.sleep(self.backoff_factor * (2 ** (attempts - 1)))
        return HarvestResult(target, None, None, attempts, self._latency(started), str(last_error))

    async def crawl(self, targets: Sequence[CrawlTarget]) -> list[HarvestResult]:
        """Fetch targets concurrently while preserving input order."""
        return list(await asyncio.gather(*(self.fetch(target) for target in targets)))

    async def _fetch_static(self, target: CrawlTarget) -> tuple[str, int]:
        if self._client is None:
            raise RuntimeError("HTTP client is not initialized")
        await self._throttle(target.url.host or "")
        response = await self._client.get(str(target.url))
        if response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {response.status_code} for {target.url}", request=response.request, response=response
            )
        return response.text, response.status_code

    async def _fetch_dynamic(self, target: CrawlTarget) -> tuple[str, int]:
        await self._throttle(target.url.host or "")
        if self._browser is None:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
        page = await self._browser.new_page()
        try:
            response = await page.goto(str(target.url), wait_until="networkidle", timeout=int(self.timeout * 1000))
            return await page.content(), response.status if response else 200
        finally:
            await page.close()

    async def _throttle(self, domain: str) -> None:
        if self.per_domain_delay == 0:
            return
        lock = self._domain_locks.setdefault(domain, asyncio.Lock())
        loop = asyncio.get_running_loop()
        async with lock:
            now = loop.time()
            wait_for = max(0.0, self._next_domain_time.get(domain, now) - now)
            if wait_for:
                await asyncio.sleep(wait_for)
            self._next_domain_time[domain] = loop.time() + self.per_domain_delay

    @staticmethod
    def _latency(started: float) -> float:
        return round((time.perf_counter() - started) * 1000, 3)

    @staticmethod
    def _retryable(error: Exception) -> bool:
        if isinstance(error, httpx.HTTPStatusError):
            return error.response.status_code == 429 or error.response.status_code >= 500
        return isinstance(error, (httpx.RequestError, TimeoutError, OSError))
