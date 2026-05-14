from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

SCRIPT_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
DROPPED_QUERY_KEYS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
}


@dataclass(slots=True)
class LinkDiscovery:
    asset_links: list[str]
    page_links: list[str]
    iframe_links: list[str]


class DiscoveryParser:
    """Extract links from HTML including static script-embedded URLs."""

    def extract(self, base_url: str, html_text: str) -> LinkDiscovery:
        soup = BeautifulSoup(html_text, "html.parser")
        links: list[str] = []
        iframe_links: list[str] = []

        for tag, attr in (
            ("a", "href"),
            ("img", "src"),
            ("source", "src"),
            ("video", "src"),
            ("audio", "src"),
            ("iframe", "src"),
        ):
            for element in soup.find_all(tag):
                value = element.get(attr)
                if not value:
                    continue
                absolute = self.normalize_url(urljoin(base_url, value))
                links.append(absolute)
                if tag == "iframe":
                    iframe_links.append(absolute)

        for script in soup.find_all("script"):
            script_text = script.string or script.text or ""
            for match in SCRIPT_URL_RE.findall(script_text):
                links.append(self.normalize_url(match))

        page_links: list[str] = []
        asset_links: list[str] = []
        for link in links:
            if not link.startswith("http"):
                continue
            if self.looks_like_listing_or_page(link):
                page_links.append(link)
            else:
                asset_links.append(link)

        return LinkDiscovery(
            asset_links=self._dedupe(asset_links),
            page_links=self._dedupe(page_links),
            iframe_links=self._dedupe(iframe_links),
        )

    @staticmethod
    def normalize_url(url: str) -> str:
        parsed = urlparse(url.strip())
        filtered_qs = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in DROPPED_QUERY_KEYS
        ]
        normalized_qs = urlencode(filtered_qs, doseq=True)
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc.lower(),
                parsed.path,
                parsed.params,
                normalized_qs,
                "",
            ),
        )

    @staticmethod
    def looks_like_listing_or_page(url: str) -> bool:
        parsed = urlparse(url)
        path = parsed.path.lower()
        return path.endswith("/") or any(
            path.endswith(suffix)
            for suffix in (".html", ".htm", ".php", ".asp", ".aspx", ".jsp")
        )

    @staticmethod
    def _dedupe(values: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
        return ordered
