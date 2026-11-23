"""Pydantic models used by the harvesting pipeline."""

from __future__ import annotations

import hashlib
import json
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class ScrapedDocument(BaseModel):
    """Validated content extracted from one crawl target."""

    model_config = ConfigDict(strict=True, extra="forbid")

    url: HttpUrl
    title: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)
    text: str = Field(min_length=1)
    tables: list[list[str]] = Field(default_factory=list)
    outbound_links: list[HttpUrl] = Field(default_factory=list)
    content_fingerprint: str = Field(default="", pattern=r"^[0-9a-f]{64}$")

    @field_validator("title", "text")
    @classmethod
    def require_non_whitespace(cls, value: str) -> str:
        """Reject text fields that contain only whitespace."""
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("value must contain non-whitespace text")
        return normalized

    @field_validator("metadata")
    @classmethod
    def normalize_metadata(cls, value: dict[str, str]) -> dict[str, str]:
        """Normalize metadata values before fingerprinting."""
        return {key.strip(): " ".join(item.split()) for key, item in value.items() if key.strip()}

    @model_validator(mode="after")
    def set_content_fingerprint(self) -> "ScrapedDocument":
        """Set and verify the SHA-256 content fingerprint."""
        fingerprint_payload = {
            "title": self.title,
            "metadata": self.metadata,
            "text": self.text,
            "tables": self.tables,
        }
        canonical = json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if self.content_fingerprint and self.content_fingerprint != expected:
            raise ValueError("content_fingerprint does not match normalized content")
        self.content_fingerprint = expected
        return self


class CrawlTarget(BaseModel):
    """A URL and the fetch policy applied to it."""

    model_config = ConfigDict(strict=True, extra="forbid")

    url: HttpUrl
    render_js: bool = False
    max_retries: int = Field(default=2, ge=0, le=10)


class PipelineMetrics(BaseModel):
    """Latency measurements collected during one pipeline run."""

    model_config = ConfigDict(strict=True, extra="forbid")

    min_ms: float = Field(ge=0)
    average_ms: float = Field(ge=0)
    p95_ms: float = Field(ge=0)
    max_ms: float = Field(ge=0)


class QAReport(BaseModel):
    """Quality and delivery summary for one pipeline run."""

    model_config = ConfigDict(strict=True, extra="forbid")

    total_targets: int = Field(ge=0)
    successful_documents: int = Field(ge=0)
    failed_targets: int = Field(ge=0)
    duplicate_count: int = Field(ge=0)
    success_rate: float = Field(ge=0, le=100)
    schema_rejections: dict[str, int] = Field(default_factory=dict)
    latency: PipelineMetrics
    exported_files: dict[str, str] = Field(default_factory=dict)
