from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

from database.db import Database
from scraper.asset_classifier import classify_asset
from scraper.discovery_parser import DiscoveryParser
from scraper.evidence_grader import EvidenceGrader
from scraper.models import DiscoveredAsset
from scraper.settings import CrawlConfig

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class CrawlResult:
    pages_crawled: int
    assets_discovered: int


class DomainCrawler:
    def __init__(
        self,
        db: Database,
        config: CrawlConfig,
        evidence_grader: EvidenceGrader,
    ) -> None:
        self.db = db
        self.config = config
        self.evidence_grader = evidence_grader
        self.discovery_parser = DiscoveryParser()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ProjectHiddenThreadsBot/0.1"})

    def crawl(self, crawl_session_id: int) -> CrawlResult:
        pages_crawled = 0
        assets_discovered = 0

        for domain_url in self.config.domains:
            domain = urlparse(domain_url).netloc.lower()
            self.db.upsert_source_domain(domain)
            robots = (
                self._load_robots_parser(domain_url)
                if self.config.respect_robots_txt
                else None
            )

            queue: deque[tuple[str, int]] = deque()
            for page_url in self._seed_urls(domain_url):
                queue.append((page_url, 0))
            visited: set[str] = set()

            while queue and len(visited) < self.config.max_pages_per_domain:
                current_url, depth = queue.popleft()
                canonical = self._canonicalize(current_url)
                if canonical in visited:
                    continue
                visited.add(canonical)

                if depth > self.config.max_depth:
                    continue
                if not self._should_visit_url(canonical, domain):
                    continue
                if robots and not robots.can_fetch("*", canonical):
                    continue

                response = self._safe_get(canonical)
                if response is None:
                    continue

                pages_crawled += 1
                content_type = response.headers.get("Content-Type", "")

                if "text/html" not in content_type.lower() and response.url:
                    asset_type = classify_asset(response.url, content_type)
                    source_domain = urlparse(response.url).netloc.lower()
                    evidence_grade = self.evidence_grader.grade(
                        source_domain=source_domain,
                        asset_type=asset_type,
                        url=response.url,
                    )
                    asset = DiscoveredAsset(
                        url=response.url,
                        parent_page=None,
                        source_domain=source_domain,
                        asset_type=asset_type,
                        mime_type=content_type,
                        evidence_tier=evidence_grade.tier,
                        evidence_rationale=evidence_grade.rationale,
                        discovered_at=datetime.utcnow(),
                    )
                    asset_id = self.db.upsert_discovered_asset(asset)
                    self.db.append_discovery_event(crawl_session_id, asset_id, asset)
                    assets_discovered += 1
                    continue

                discovery = self.discovery_parser.extract(response.url, response.text)

                for asset_url in discovery.asset_links + discovery.iframe_links:
                    asset_type = classify_asset(asset_url)
                    source_domain = urlparse(asset_url).netloc.lower()
                    evidence_grade = self.evidence_grader.grade(
                        source_domain=source_domain,
                        asset_type=asset_type,
                        url=asset_url,
                    )
                    asset = DiscoveredAsset(
                        url=asset_url,
                        parent_page=canonical,
                        source_domain=source_domain,
                        asset_type=asset_type,
                        evidence_tier=evidence_grade.tier,
                        evidence_rationale=evidence_grade.rationale,
                        discovered_at=datetime.utcnow(),
                    )
                    asset_id = self.db.upsert_discovered_asset(asset)
                    self.db.append_discovery_event(crawl_session_id, asset_id, asset)
                    assets_discovered += 1

                for page_url in discovery.page_links:
                    queue.append((page_url, depth + 1))

            LOGGER.info(
                "Crawl complete for domain",
                extra={"domain": domain, "pages": len(visited)},
            )

        return CrawlResult(
            pages_crawled=pages_crawled, assets_discovered=assets_discovered
        )

    def _safe_get(self, url: str) -> requests.Response | None:
        try:
            if self.config.request_delay_seconds > 0:
                time.sleep(self.config.request_delay_seconds)
            return self.session.get(url, timeout=self.config.request_timeout_seconds)
        except requests.RequestException as exc:
            LOGGER.warning("Request failed", extra={"url": url, "error": str(exc)})
            return None

    def _canonicalize(self, url: str) -> str:
        return self.discovery_parser.normalize_url(url)

    def _should_visit_url(self, url: str, root_domain: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False

        netloc = parsed.netloc.lower()
        if self.config.blocked_domains and any(
            netloc.endswith(blocked) for blocked in self.config.blocked_domains
        ):
            return False

        if self.config.allowed_domains:
            return any(
                netloc.endswith(allowed) for allowed in self.config.allowed_domains
            )

        return netloc.endswith(root_domain)

    def _load_robots_parser(self, domain_url: str) -> RobotFileParser | None:
        parsed = urlparse(domain_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = RobotFileParser()
        parser.set_url(robots_url)
        try:
            parser.read()
            return parser
        except Exception as exc:
            LOGGER.warning(
                "Robots parse failed",
                extra={"robots_url": robots_url, "error": str(exc)},
            )
            return None

    def _seed_urls(self, domain_url: str) -> list[str]:
        seeds = [domain_url]
        if not self.config.pagination_templates:
            return seeds

        start = max(1, self.config.pagination_start)
        end = max(start, self.config.pagination_end)

        for template in self.config.pagination_templates:
            for page in range(start, end + 1):
                generated = template.format(page=page)
                if generated.startswith("http"):
                    seeds.append(generated)
                else:
                    seeds.append(
                        self.discovery_parser.normalize_url(
                            urljoin(domain_url, generated)
                        )
                    )

        deduped: list[str] = []
        seen: set[str] = set()
        for seed in seeds:
            canonical = self._canonicalize(seed)
            if canonical in seen:
                continue
            seen.add(canonical)
            deduped.append(canonical)
        return deduped
