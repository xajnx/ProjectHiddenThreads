from __future__ import annotations

import argparse
import logging
from pathlib import Path

from analysis.entity_extractor import EntityExtractor
from analysis.reporting import ReportGenerator
from database.db import Database
from scraper.crawler import DomainCrawler
from scraper.document_parser import DocumentParser
from scraper.downloader import AssetDownloader
from scraper.evidence_grader import EvidenceGrader, EvidenceGradingConfig
from scraper.image_parser import ImageParser
from scraper.local_release_importer import LocalReleaseImporter
from scraper.logging_utils import setup_logging
from scraper.seed_importer import SeedImporter
from scraper.settings import AppConfig, load_config
from scraper.verifier import AssetVerifier

LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ProjectHiddenThreads acquisition and archival pipeline",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="Path to the YAML configuration file.",
    )
    parser.add_argument(
        "--skip-crawl",
        action="store_true",
        help="Skip discovery and only process queued downloads.",
    )
    parser.add_argument(
        "--seed-url-file",
        type=Path,
        default=None,
        help="Optional path to a TXT/CSV file of authorized asset URLs to queue for download.",
    )
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help="Skip verification phase and retain existing asset states.",
    )
    parser.add_argument(
        "--import-local-release-dir",
        type=Path,
        default=None,
        help="Optional path to a local release directory to ingest into canonical raw storage and the asset index.",
    )
    parser.add_argument(
        "--skip-parse",
        action="store_true",
        help="Skip local text extraction for downloaded/imported document assets.",
    )
    parser.add_argument(
        "--skip-analysis",
        action="store_true",
        help="Skip text analysis and entity/event extraction from parsed outputs.",
    )
    return parser.parse_args()


def ensure_required_directories(config: AppConfig, project_root: Path) -> None:
    required_paths = [
        config.data.raw_documents_dir,
        config.data.raw_videos_dir,
        config.data.raw_audio_dir,
        config.data.raw_images_dir,
        config.data.raw_archives_dir,
        config.data.versions_dir,
        config.data.database_path.parent,
        project_root / "logs",
        project_root / "data" / "parsed_text",
        project_root / "data" / "metadata",
        project_root / "data" / "embeddings",
        project_root / "data" / "reports",
    ]

    for path in required_paths:
        path.mkdir(parents=True, exist_ok=True)


def run_pipeline(
    config_path: Path,
    skip_crawl: bool,
    seed_url_file: Path | None,
    skip_verify: bool,
    import_local_release_dir: Path | None,
    skip_parse: bool,
    skip_analysis: bool,
) -> int:
    project_root = Path(__file__).resolve().parent

    setup_logging(project_root / "config" / "logging.yaml")

    if not config_path.exists():
        LOGGER.error(
            "Missing config file. Copy config/config.yaml.example to config/config.yaml and update it.",
        )
        return 1

    config = load_config(config_path, project_root)
    ensure_required_directories(config, project_root)

    database = Database(
        db_path=config.data.database_path,
        schema_path=project_root / "database" / "schema.sql",
    )
    database.initialize()

    crawl_session_id = database.start_crawl_session(config.crawl.domains)
    pages_crawled = 0
    assets_discovered = 0
    assets_downloaded = 0
    assets_blocked = 0
    crawl_status = "completed"

    try:
        evidence_grader = EvidenceGrader(
            EvidenceGradingConfig(
                enabled=config.evidence.enabled,
                government_domain_suffixes=config.evidence.government_domain_suffixes,
                tier1_asset_types=config.evidence.tier1_asset_types,
                domain_tier_overrides=config.evidence.domain_tier_overrides,
            ),
        )

        if seed_url_file:
            seed_importer = SeedImporter(database, evidence_grader)
            seed_result = seed_importer.import_file(seed_url_file, crawl_session_id)
            assets_discovered += seed_result.imported

        if import_local_release_dir:
            local_release_importer = LocalReleaseImporter(
                database,
                config,
                evidence_grader,
            )
            local_import_result = local_release_importer.import_directory(
                import_local_release_dir,
                crawl_session_id,
            )
            assets_discovered += local_import_result.imported
            assets_downloaded += (
                local_import_result.imported + local_import_result.updated
            )

        if not skip_crawl:
            crawler = DomainCrawler(database, config.crawl, evidence_grader)
            crawl_result = crawler.crawl(crawl_session_id)
            pages_crawled = crawl_result.pages_crawled
            assets_discovered += crawl_result.assets_discovered

        if not skip_verify:
            verifier = AssetVerifier(database, config)
            verification_result = verifier.run(crawl_session_id)
            assets_blocked += verification_result.blocked
            LOGGER.info(
                "Verification summary",
                extra={
                    "crawl_session_id": crawl_session_id,
                    "new": verification_result.new,
                    "unchanged": verification_result.unchanged,
                    "modified": verification_result.modified,
                    "blocked": verification_result.blocked,
                    "unknown": verification_result.unknown,
                },
            )

        downloader = AssetDownloader(database, config)
        download_result = downloader.run_downloads(crawl_session_id)
        assets_downloaded += download_result.downloaded
        assets_blocked += download_result.blocked

        if not skip_parse:
            document_parser = DocumentParser(database, project_root)
            document_parse_result = document_parser.run(crawl_session_id)
            LOGGER.info(
                "Document parsing summary",
                extra={
                    "crawl_session_id": crawl_session_id,
                    "parsed": document_parse_result.parsed,
                    "skipped": document_parse_result.skipped,
                    "failed": document_parse_result.failed,
                    "unsupported": document_parse_result.unsupported,
                },
            )

            image_parser = ImageParser(database, project_root)
            image_parse_result = image_parser.run(crawl_session_id)
            LOGGER.info(
                "Image parsing summary",
                extra={
                    "crawl_session_id": crawl_session_id,
                    "parsed": image_parse_result.parsed,
                    "skipped": image_parse_result.skipped,
                    "failed": image_parse_result.failed,
                    "unsupported": image_parse_result.unsupported,
                },
            )

        if not skip_analysis:
            entity_extractor = EntityExtractor(database, project_root)
            extraction_result = entity_extractor.run(crawl_session_id)
            LOGGER.info(
                "Entity extraction summary",
                extra={
                    "crawl_session_id": crawl_session_id,
                    "analyzed": extraction_result.analyzed,
                    "skipped": extraction_result.skipped,
                    "failed": extraction_result.failed,
                    "entities_written": extraction_result.entities_written,
                    "events_written": extraction_result.events_written,
                },
            )

        report_generator = ReportGenerator(database, project_root)
        report_paths = report_generator.generate()

        LOGGER.info(
            "Pipeline summary",
            extra={
                "crawl_session_id": crawl_session_id,
                "pages_crawled": pages_crawled,
                "assets_discovered": assets_discovered,
                "downloaded": download_result.downloaded,
                "skipped_unchanged": download_result.skipped_unchanged,
                "blocked": download_result.blocked,
                "failed": download_result.failed,
                "report_markdown": str(report_paths.markdown_path),
                "report_html": str(report_paths.html_path),
                "json_index": str(report_paths.json_index_path),
            },
        )
    except Exception:
        crawl_status = "failed"
        LOGGER.exception("Pipeline execution failed")
        raise
    finally:
        database.finish_crawl_session(
            crawl_session_id,
            pages_crawled=pages_crawled,
            assets_discovered=assets_discovered,
            assets_downloaded=assets_downloaded,
            assets_blocked=assets_blocked,
            status=crawl_status,
        )

    return 0


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(
        run_pipeline(
            args.config,
            args.skip_crawl,
            args.seed_url_file,
            args.skip_verify,
            args.import_local_release_dir,
            args.skip_parse,
            args.skip_analysis,
        ),
    )
