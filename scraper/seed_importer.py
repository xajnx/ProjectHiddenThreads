from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from database.db import Database
from scraper.asset_classifier import classify_asset, is_downloadable_asset
from scraper.evidence_grader import EvidenceGrader
from scraper.models import DiscoveredAsset

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class SeedImportResult:
    imported: int = 0
    skipped_invalid: int = 0
    skipped_unsupported: int = 0


class SeedImporter:
    """Imports externally provided URLs into the asset index for bulk downloading."""

    def __init__(self, db: Database, evidence_grader: EvidenceGrader) -> None:
        self.db = db
        self.evidence_grader = evidence_grader

    def import_file(self, file_path: Path, crawl_session_id: int) -> SeedImportResult:
        urls = self._parse_url_file(file_path)
        result = SeedImportResult()

        for raw_url in urls:
            url = raw_url.strip()
            if not url:
                continue

            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                result.skipped_invalid += 1
                continue

            asset_type = classify_asset(url)
            if not is_downloadable_asset(asset_type):
                result.skipped_unsupported += 1
                continue

            source_domain = parsed.netloc.lower()
            grade = self.evidence_grader.grade(
                source_domain=source_domain,
                asset_type=asset_type,
                url=url,
            )
            discovered_asset = DiscoveredAsset(
                url=url,
                parent_page="seed_import",
                source_domain=source_domain,
                asset_type=asset_type,
                evidence_tier=grade.tier,
                evidence_rationale=grade.rationale,
                discovered_at=datetime.utcnow(),
            )
            asset_id = self.db.upsert_discovered_asset(discovered_asset)
            self.db.append_ledger_event(
                event_type="seed_import",
                actor="seed_importer",
                payload={
                    "url": url,
                    "asset_type": asset_type,
                    "source_file": str(file_path),
                },
                crawl_session_id=crawl_session_id,
                asset_id=asset_id,
            )
            result.imported += 1

        LOGGER.info(
            "Seed import completed",
            extra={
                "source_file": str(file_path),
                "imported": result.imported,
                "skipped_invalid": result.skipped_invalid,
                "skipped_unsupported": result.skipped_unsupported,
            },
        )
        return result

    @staticmethod
    def _parse_url_file(file_path: Path) -> list[str]:
        if not file_path.exists():
            raise FileNotFoundError(f"Seed URL file not found: {file_path}")

        suffix = file_path.suffix.lower()
        if suffix == ".csv":
            with file_path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.reader(handle)
                rows = list(reader)
            if not rows:
                return []

            header = [col.strip().lower() for col in rows[0]]
            if "url" in header:
                idx = header.index("url")
                return [row[idx] for row in rows[1:] if len(row) > idx]
            return [row[0] for row in rows if row]

        lines = file_path.read_text(encoding="utf-8").splitlines()
        return [
            line for line in lines if line.strip() and not line.strip().startswith("#")
        ]
