from pathlib import Path

from dataharvest.parsers import DOMParser


FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_parser_extracts_clean_article_fields() -> None:
    payload = DOMParser().parse((FIXTURES / "clean_article.html").read_bytes(), "https://example.test/news/q3")

    assert payload["title"] == "Harbor Signals: Q3 Brief"
    assert "1.8 million containers" in payload["text"]
    assert payload["metadata"]["description"] == "A quarterly shipping market brief."
    assert payload["tables"] == [["Metric | Value", "Throughput | 1.8m"]]
    assert payload["outbound_links"] == ["https://example.test/reports/q2", "https://example.org/methodology"]


def test_parser_handles_hydrated_fragment_without_html_shell() -> None:
    payload = DOMParser().parse((FIXTURES / "js_hydrated_fragment.html").read_text(encoding="utf-8"), "https://example.test/product/1")

    assert payload["title"] == "Northwind Field Sensor"
    assert "two-year battery" in payload["text"]
    assert payload["outbound_links"] == ["https://example.test/support"]


def test_parser_handles_malformed_dom() -> None:
    payload = DOMParser().parse((FIXTURES / "malformed_dom.html").read_text(encoding="utf-8"), "https://example.test/report")

    assert payload["title"] == "Unclosed Report"
    assert "Records remain available" in payload["text"]
    assert payload["outbound_links"] == ["https://example.test/details"]
