"""Command-line entry point for dataharvest-qa."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from dataharvest.pipeline import HarvestPipeline
from dataharvest.scraper import AsyncHarvestEngine


FIXTURE_DIR = ROOT_DIR / "fixtures"


def _offline_documents() -> list[tuple[str, str]]:
    names = ["clean_article.html", "js_hydrated_fragment.html", "malformed_dom.html", "duplicate_article.html", "invalid_empty.html"]
    return [(f"https://offline.dataharvest.test/{name}", (FIXTURE_DIR / name).read_text(encoding="utf-8")) for name in names]


def _read_urls(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")]


async def _run(args: argparse.Namespace) -> None:
    output_dir = Path(args.output_dir)
    if args.offline_demo:
        async with AsyncHarvestEngine(max_concurrency=4) as engine:
            report = await HarvestPipeline(engine, output_dir).run_html_documents(_offline_documents(), "offline-demo")
    else:
        if not args.urls_file:
            raise SystemExit("provide --urls-file or use --offline-demo")
        from dataharvest.schemas import CrawlTarget

        targets = [CrawlTarget(url=url, render_js=args.render_js, max_retries=args.max_retries) for url in _read_urls(Path(args.urls_file))]
        async with AsyncHarvestEngine(
            max_concurrency=args.concurrency,
            per_domain_delay=args.domain_delay,
            backoff_factor=args.backoff,
        ) as engine:
            report = await HarvestPipeline(engine, output_dir).run(targets, "harvest")
    print(report.model_dump_json(indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Asynchronous HTML harvesting and QA validation")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline-demo", action="store_true", help="run against bundled fixtures")
    mode.add_argument("--urls-file", type=Path, help="read one URL per line")
    parser.add_argument("--output-dir", default="output", help="directory for exports")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--domain-delay", type=float, default=0.0)
    parser.add_argument("--backoff", type=float, default=0.25)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--render-js", action="store_true", help="use Playwright for URL targets")
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
