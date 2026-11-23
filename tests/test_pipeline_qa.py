import asyncio
import csv
import json
import time
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from dataharvest.pipeline import HarvestPipeline
from dataharvest.schemas import CrawlTarget, ScrapedDocument
from dataharvest.scraper import AsyncHarvestEngine


def test_scraped_document_rejects_empty_text_and_fingerprints_content() -> None:
    document = ScrapedDocument(url="https://example.test/a", title="A", text="Body")
    assert len(document.content_fingerprint) == 64
    with pytest.raises(ValidationError):
        ScrapedDocument(url="https://example.test/a", title="A", text="   ")


def test_async_engine_respects_concurrency_limit() -> None:
    active = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return httpx.Response(200, text="<h1>Ok</h1><p>Content</p>", request=request)

    async def scenario() -> None:
        transport = httpx.MockTransport(handler)
        async with AsyncHarvestEngine(max_concurrency=2, client=httpx.AsyncClient(transport=transport)) as engine:
            targets = [CrawlTarget(url=f"https://example.test/{index}") for index in range(6)]
            results = await engine.crawl(targets)
        assert all(result.error is None for result in results)
        assert peak == 2

    asyncio.run(scenario())


def test_retry_backoff_retries_transient_errors() -> None:
    calls = 0
    sleeps: list[float] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, text="busy", request=request)
        return httpx.Response(200, text="<h1>Ok</h1><p>Content</p>", request=request)

    async def scenario() -> None:
        transport = httpx.MockTransport(handler)
        original_sleep = asyncio.sleep

        async def record_sleep(delay: float) -> None:
            sleeps.append(delay)
            await original_sleep(0)

        original_engine_sleep = asyncio.sleep
        asyncio.sleep = record_sleep
        try:
            async with AsyncHarvestEngine(client=httpx.AsyncClient(transport=transport), backoff_factor=0.1) as engine:
                result = await engine.fetch(CrawlTarget(url="https://example.test/retry", max_retries=2))
        finally:
            asyncio.sleep = original_engine_sleep
        assert result.error is None
        assert result.attempts == 3
        assert sleeps == [0.1, 0.2]

    asyncio.run(scenario())


def test_pipeline_deduplicates_rejects_and_exports(tmp_path: Path) -> None:
    fixture_dir = Path(__file__).parents[1] / "fixtures"
    clean = (fixture_dir / "clean_article.html").read_text(encoding="utf-8")
    invalid = (fixture_dir / "invalid_empty.html").read_text(encoding="utf-8")

    async def scenario():
        async with AsyncHarvestEngine() as engine:
            pipeline = HarvestPipeline(engine, tmp_path)
            return await pipeline.run_html_documents(
                [("https://example.test/one", clean), ("https://example.test/two", clean), ("https://example.test/empty", invalid)]
            )

    report = asyncio.run(scenario())
    assert report.successful_documents == 1
    assert report.duplicate_count == 1
    assert report.failed_targets == 1
    assert report.schema_rejections["title"] == 1
    assert report.schema_rejections["text"] == 1
    assert report.success_rate == pytest.approx(66.67)

    exported = json.loads((tmp_path / "harvest.json").read_text(encoding="utf-8"))
    assert len(exported) == 1
    with (tmp_path / "harvest.csv").open(newline="", encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == 1
    assert len((tmp_path / "harvest.jsonl").read_text(encoding="utf-8").splitlines()) == 1
    assert (tmp_path / "harvest.qa.json").exists()
