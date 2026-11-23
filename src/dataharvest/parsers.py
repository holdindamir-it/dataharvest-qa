"""DOM parsing and text normalization."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


class DOMParser:
    """Extract normalized document fields from HTML."""

    _boilerplate_tags = ("script", "style", "nav", "aside", "footer", "form", "noscript", "template")
    _noise_tokens = ("cookie", "breadcrumb", "social", "share", "newsletter", "advert", "promo")

    def __init__(self, parser: str = "lxml") -> None:
        self.parser = parser if parser in {"lxml", "html.parser"} else "lxml"

    def parse(self, html: str | bytes, url: str) -> dict[str, Any]:
        """Parse HTML into fields accepted by ``ScrapedDocument``."""
        soup = self._soup(html)
        self._remove_boilerplate(soup)
        return {
            "url": url,
            "title": self._title(soup),
            "metadata": self._metadata(soup),
            "text": self._article_text(soup),
            "tables": self._tables(soup),
            "outbound_links": self._links(soup, url),
        }

    def _soup(self, html: str | bytes) -> BeautifulSoup:
        if isinstance(html, bytes):
            html = html.decode("utf-8", errors="replace")
        try:
            return BeautifulSoup(html, self.parser)
        except Exception:
            return BeautifulSoup(html, "html.parser")

    def _remove_boilerplate(self, soup: BeautifulSoup) -> None:
        for element in soup(self._boilerplate_tags):
            element.decompose()
        for element in soup.find_all(True):
            if element.parent is None:
                continue
            class_text = " ".join(element.get("class", []))
            element_id = element.get("id", "")
            marker = f"{class_text} {element_id}".lower()
            if any(token in marker for token in self._noise_tokens):
                element.decompose()

    def _title(self, soup: BeautifulSoup) -> str:
        heading = soup.find("h1")
        title = next(heading.stripped_strings, "") if heading else ""
        if not title and soup.title:
            title = soup.title.get_text(" ", strip=True)
        return self._clean_text(title)

    def _metadata(self, soup: BeautifulSoup) -> dict[str, str]:
        metadata: dict[str, str] = {}
        for tag in soup.find_all("meta"):
            key = tag.get("name") or tag.get("property")
            content = tag.get("content")
            if key and content:
                metadata[self._clean_text(str(key)).lower()] = self._clean_text(str(content))
        return {key: value for key, value in metadata.items() if value}

    def _article_text(self, soup: BeautifulSoup) -> str:
        root = soup.find(["article", "main"]) or soup.find(class_=re.compile(r"article|product|content", re.I)) or soup.body or soup
        for element in root.find_all(["h1", "h2", "h3", "p", "li", "blockquote", "dd", "dt"]):
            text = self._clean_text(element.get_text(" ", strip=True))
            if text:
                element.replace_with(f" {text} ")
        return self._clean_text(root.get_text(" ", strip=True))

    def _tables(self, soup: BeautifulSoup) -> list[list[str]]:
        tables: list[list[str]] = []
        for table in soup.find_all("table"):
            rows: list[str] = []
            for row in table.find_all("tr"):
                cells = [self._clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"])]
                if cells:
                    rows.append(" | ".join(cells))
            if rows:
                tables.append(rows)
        return tables

    def _links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        links: list[str] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            href = urljoin(base_url, str(anchor["href"]))
            parsed = urlparse(href)
            normalized = href.split("#", 1)[0]
            if parsed.scheme in {"http", "https"} and normalized not in seen:
                links.append(normalized)
                seen.add(normalized)
        return links

    @staticmethod
    def _clean_text(value: str) -> str:
        return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()
