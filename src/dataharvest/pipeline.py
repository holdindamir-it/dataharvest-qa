"""Pipeline orchestration, QA accounting, and atomic exports."""

from __future__ import annotations

import asyncio
import csv
import json
import math
import os
import tempfile
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .parsers import DOMParser
from .schemas import CrawlTarget, PipelineMetrics, QAReport, ScrapedDocument
from .scraper import AsyncHarvestEngine, HarvestResult


class HarvestPipeline:
    """Run fetch, parse, validation, deduplication, and export stages."""

    def __init__(self, engine: AsyncHarvestEngine, output_dir: str | Path = "output", parser: DOMParser | None = None) -> None:
        self.engine = engine
        self.output_dir = Path(output_dir)
        self.parser = parser or DOMParser()

    async def run(self, targets: Sequence[CrawlTarget], export_stem: str = "harvest") -> QAReport:
        """Run the live crawl pipeline."""
        results = await self.engine.crawl(targets)
        documents, latencies, rejections, failures, duplicates = await self._process_results(results)
        return self._finalize(len(targets), documents, latencies, rejections, failures, duplicates, export_stem)

    async def run_html_documents(
        self, documents: Sequence[tuple[str, str | bytes]], export_stem: str = "harvest"
    ) -> QAReport:
        """Run the parser and QA stages against in-memory HTML."""
        results: list[HarvestResult] = []
        for url, html in documents:
            target = CrawlTarget(url=url)
            results.append(HarvestResult(target, html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html, 200, 1, 0.0))
        parsed, latencies, rejections, failures, duplicates = await self._process_results(results)
        return self._finalize(len(documents), parsed, latencies, rejections, failures, duplicates, export_stem)

    async def _process_results(
        self, results: Sequence[HarvestResult]
    ) -> tuple[list[ScrapedDocument], list[float], Counter[str], int, int]:
        documents: list[ScrapedDocument] = []
        latencies: list[float] = []
        rejections: Counter[str] = Counter()
        failures = 0
        duplicates = 0
        fingerprints: set[str] = set()
        for result in results:
            latencies.append(result.latency_ms)
            if result.error or result.html is None:
                failures += 1
                rejections["fetch_error"] += 1
                continue
            try:
                payload = await asyncio.to_thread(self.parser.parse, result.html, str(result.target.url))
                document = ScrapedDocument.model_validate(payload)
            except ValidationError as exc:
                failures += 1
                for error in exc.errors():
                    field = ".".join(str(part) for part in error["loc"]) or "model"
                    rejections[field] += 1
                continue
            except Exception as exc:
                failures += 1
                rejections[f"parser:{type(exc).__name__}"] += 1
                continue
            if document.content_fingerprint in fingerprints:
                duplicates += 1
                continue
            fingerprints.add(document.content_fingerprint)
            documents.append(document)
        return documents, latencies, rejections, failures, duplicates

    def _finalize(
        self,
        total_targets: int,
        documents: Sequence[ScrapedDocument],
        latencies: Sequence[float],
        rejections: Counter[str],
        failures: int,
        duplicates: int,
        export_stem: str,
    ) -> QAReport:
        exported_files = self._export(documents, export_stem)
        report = QAReport(
            total_targets=total_targets,
            successful_documents=len(documents),
            failed_targets=failures,
            duplicate_count=duplicates,
            success_rate=round((len(documents) + duplicates) / total_targets * 100, 2) if total_targets else 0.0,
            schema_rejections=dict(rejections),
            latency=self._metrics(latencies),
            exported_files=exported_files,
        )
        self._atomic_write_text(
            self.output_dir / f"{export_stem}.qa.json",
            json.dumps(report.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        )
        return report

    def _export(self, documents: Sequence[ScrapedDocument], export_stem: str) -> dict[str, str]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        rows = [document.model_dump(mode="json") for document in documents]
        json_path = self.output_dir / f"{export_stem}.json"
        jsonl_path = self.output_dir / f"{export_stem}.jsonl"
        csv_path = self.output_dir / f"{export_stem}.csv"
        self._atomic_write_text(json_path, json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
        self._atomic_write_text(jsonl_path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
        self._atomic_write_csv(csv_path, rows)
        return {"json": str(json_path), "jsonl": str(jsonl_path), "csv": str(csv_path)}

    def _atomic_write_csv(self, path: Path, rows: Sequence[dict[str, Any]]) -> None:
        fields = ["url", "title", "metadata", "text", "tables", "outbound_links", "content_fingerprint"]
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=self.output_dir, delete=False) as handle:
            temp_name = handle.name
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow({field: json.dumps(row[field], ensure_ascii=False) if isinstance(row[field], (dict, list)) else row[field] for field in fields})
        os.replace(temp_name, path)

    @staticmethod
    def _atomic_write_text(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            handle.write(text)
            temp_name = handle.name
        os.replace(temp_name, path)

    @staticmethod
    def _metrics(latencies: Sequence[float]) -> PipelineMetrics:
        if not latencies:
            return PipelineMetrics(min_ms=0.0, average_ms=0.0, p95_ms=0.0, max_ms=0.0)
        ordered = sorted(latencies)
        p95_index = min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)
        return PipelineMetrics(
            min_ms=round(ordered[0], 3),
            average_ms=round(sum(ordered) / len(ordered), 3),
            p95_ms=round(ordered[p95_index], 3),
            max_ms=round(ordered[-1], 3),
        )
