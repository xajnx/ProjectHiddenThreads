from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class CrawlConfig:
    domains: list[str]
    max_depth: int
    allowed_domains: list[str]
    blocked_domains: list[str]
    respect_robots_txt: bool
    request_timeout_seconds: float
    request_delay_seconds: float
    max_pages_per_domain: int
    pagination_start: int
    pagination_end: int
    pagination_templates: list[str]


@dataclass(slots=True)
class DownloadConfig:
    max_workers: int
    retries: int
    backoff_factor: float
    chunk_size_bytes: int


@dataclass(slots=True)
class DataConfig:
    raw_documents_dir: Path
    raw_videos_dir: Path
    raw_audio_dir: Path
    raw_images_dir: Path
    raw_archives_dir: Path
    versions_dir: Path
    database_path: Path


@dataclass(slots=True)
class EvidenceConfig:
    enabled: bool
    government_domain_suffixes: list[str]
    tier1_asset_types: list[str]
    domain_tier_overrides: dict[str, int]


@dataclass(slots=True)
class AppConfig:
    crawl: CrawlConfig
    download: DownloadConfig
    data: DataConfig
    evidence: EvidenceConfig


def _resolve_path(path_value: str, base_dir: Path | None) -> Path:
    path = Path(path_value)
    if path.is_absolute() or base_dir is None:
        return path
    return (base_dir / path).resolve()


def load_config(config_path: Path, base_dir: Path | None = None) -> AppConfig:
    payload: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    crawl = payload["crawl"]
    download = payload["download"]
    data = payload["data"]
    evidence = payload.get("evidence_grading", {})

    return AppConfig(
        crawl=CrawlConfig(
            domains=list(crawl["domains"]),
            max_depth=int(crawl.get("max_depth", 2)),
            allowed_domains=list(crawl.get("allowed_domains", [])),
            blocked_domains=list(crawl.get("blocked_domains", [])),
            respect_robots_txt=bool(crawl.get("respect_robots_txt", True)),
            request_timeout_seconds=float(crawl.get("request_timeout_seconds", 20.0)),
            request_delay_seconds=float(crawl.get("request_delay_seconds", 0.5)),
            max_pages_per_domain=int(crawl.get("max_pages_per_domain", 300)),
            pagination_start=int(crawl.get("pagination_start", 1)),
            pagination_end=int(crawl.get("pagination_end", 1)),
            pagination_templates=list(crawl.get("pagination_templates", [])),
        ),
        download=DownloadConfig(
            max_workers=int(download.get("max_workers", 4)),
            retries=int(download.get("retries", 3)),
            backoff_factor=float(download.get("backoff_factor", 1.0)),
            chunk_size_bytes=int(download.get("chunk_size_bytes", 1024 * 1024)),
        ),
        data=DataConfig(
            raw_documents_dir=_resolve_path(data["raw_documents_dir"], base_dir),
            raw_videos_dir=_resolve_path(data["raw_videos_dir"], base_dir),
            raw_audio_dir=_resolve_path(data["raw_audio_dir"], base_dir),
            raw_images_dir=_resolve_path(data["raw_images_dir"], base_dir),
            raw_archives_dir=_resolve_path(data["raw_archives_dir"], base_dir),
            versions_dir=_resolve_path(data["versions_dir"], base_dir),
            database_path=_resolve_path(data["database_path"], base_dir),
        ),
        evidence=EvidenceConfig(
            enabled=bool(evidence.get("enabled", True)),
            government_domain_suffixes=list(
                evidence.get("government_domain_suffixes", [".gov", ".mil"]),
            ),
            tier1_asset_types=list(
                evidence.get(
                    "tier1_asset_types",
                    ["document", "video", "audio", "archive"],
                ),
            ),
            domain_tier_overrides={
                str(key): int(value)
                for key, value in dict(
                    evidence.get("domain_tier_overrides", {})
                ).items()
            },
        ),
    )
