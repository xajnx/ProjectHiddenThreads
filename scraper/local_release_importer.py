from __future__ import annotations

import logging
import mimetypes
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from database.db import Database
from scraper.asset_classifier import (
    ASSET_TYPE_ARCHIVE,
    ASSET_TYPE_AUDIO,
    ASSET_TYPE_DOCUMENT,
    ASSET_TYPE_IMAGE,
    ASSET_TYPE_VIDEO,
    classify_asset,
    is_downloadable_asset,
)
from scraper.checksum import sha256_file
from scraper.evidence_grader import EvidenceGrader
from scraper.models import DiscoveredAsset
from scraper.settings import AppConfig
from scraper.version_tracker import VersionTracker

LOGGER = logging.getLogger(__name__)

SOURCE_DOMAIN_MAP = {
    "deptofwar": "war.gov",
    "deptofstate": "state.gov",
    "fbi": "fbi.gov",
    "nasa": "nasa.gov",
    "sources": "release.source.local",
}


@dataclass(slots=True)
class LocalReleaseImportResult:
    imported: int = 0
    unchanged: int = 0
    updated: int = 0
    skipped_unsupported: int = 0


class LocalReleaseImporter:
    def __init__(
        self,
        db: Database,
        config: AppConfig,
        evidence_grader: EvidenceGrader,
    ) -> None:
        self.db = db
        self.config = config
        self.evidence_grader = evidence_grader
        self.version_tracker = VersionTracker(config.data.versions_dir)

    def import_directory(
        self,
        release_dir: Path,
        crawl_session_id: int,
    ) -> LocalReleaseImportResult:
        if not release_dir.exists() or not release_dir.is_dir():
            raise FileNotFoundError(f"Local release directory not found: {release_dir}")

        result = LocalReleaseImportResult()
        release_name = release_dir.name

        for file_path in sorted(
            path for path in release_dir.rglob("*") if path.is_file()
        ):
            relative_path = file_path.relative_to(release_dir)
            source_name = relative_path.parts[0] if relative_path.parts else "unknown"
            source_domain = self._source_domain_for_folder(source_name)
            mime_type = mimetypes.guess_type(str(file_path))[0]
            synthetic_url = self._synthetic_url(release_name, relative_path)
            asset_type = classify_asset(synthetic_url, mime_type)

            if not is_downloadable_asset(asset_type):
                result.skipped_unsupported += 1
                continue

            grade = self.evidence_grader.grade(
                source_domain=source_domain,
                asset_type=asset_type,
                url=synthetic_url,
            )
            asset = DiscoveredAsset(
                url=synthetic_url,
                parent_page=f"local_release_import:{release_name}",
                source_domain=source_domain,
                asset_type=asset_type,
                mime_type=mime_type,
                evidence_tier=grade.tier,
                evidence_rationale=grade.rationale,
            )
            asset_id = self.db.upsert_discovered_asset(asset)
            existing = self.db.get_asset_by_url(synthetic_url)
            if existing is None:
                raise RuntimeError(
                    f"Imported asset missing after upsert: {synthetic_url}"
                )

            source_sha256 = sha256_file(file_path)
            destination_path = self._destination_path(
                asset_type=asset_type,
                release_name=release_name,
                relative_path=relative_path,
            )
            destination_path.parent.mkdir(parents=True, exist_ok=True)

            action = "imported"
            if existing["local_path"] and existing["sha256"] == source_sha256:
                current_path = Path(existing["local_path"])
                if current_path.exists():
                    destination_path = current_path
                    action = "unchanged"
                    result.unchanged += 1

            if action != "unchanged":
                if existing["local_path"]:
                    current_path = Path(existing["local_path"])
                    if (
                        current_path.exists()
                        and current_path.resolve() != destination_path.resolve()
                    ):
                        archived = self.version_tracker.archive_existing_file(
                            current_path,
                            asset_id,
                        )
                        self.db.add_asset_version(
                            asset_id,
                            previous_sha256=existing["sha256"],
                            previous_path=existing["local_path"],
                            archived_path=str(archived),
                            change_reason="local_release_relocated",
                        )
                    elif current_path.exists() and existing["sha256"] != source_sha256:
                        archived = self.version_tracker.archive_existing_file(
                            current_path,
                            asset_id,
                        )
                        self.db.add_asset_version(
                            asset_id,
                            previous_sha256=existing["sha256"],
                            previous_path=existing["local_path"],
                            archived_path=str(archived),
                            change_reason="local_release_updated",
                        )

                shutil.copy2(file_path, destination_path)
                if existing["sha256"]:
                    result.updated += 1
                    action = "updated"
                else:
                    result.imported += 1

            final_sha256 = source_sha256
            size_bytes = destination_path.stat().st_size
            self.db.record_asset_download(
                asset_id,
                sha256=final_sha256,
                size_bytes=size_bytes,
                local_path=str(destination_path),
                mime_type=mime_type,
                etag=None,
                last_modified=None,
                status="downloaded",
            )
            self.db.append_ledger_event(
                event_type="local_import",
                actor="local_release_importer",
                payload={
                    "release_name": release_name,
                    "source_file": str(file_path),
                    "destination_path": str(destination_path),
                    "source_folder": source_name,
                    "synthetic_url": synthetic_url,
                    "sha256": final_sha256,
                    "action": action,
                },
                crawl_session_id=crawl_session_id,
                asset_id=asset_id,
            )

        LOGGER.info(
            "Local release import completed",
            extra={
                "release_dir": str(release_dir),
                "imported": result.imported,
                "unchanged": result.unchanged,
                "updated": result.updated,
                "skipped_unsupported": result.skipped_unsupported,
            },
        )
        return result

    def _destination_path(
        self,
        *,
        asset_type: str,
        release_name: str,
        relative_path: Path,
    ) -> Path:
        root_map = {
            ASSET_TYPE_DOCUMENT: self.config.data.raw_documents_dir,
            ASSET_TYPE_VIDEO: self.config.data.raw_videos_dir,
            ASSET_TYPE_AUDIO: self.config.data.raw_audio_dir,
            ASSET_TYPE_IMAGE: self.config.data.raw_images_dir,
            ASSET_TYPE_ARCHIVE: self.config.data.raw_archives_dir,
        }
        root = root_map.get(asset_type, self.config.data.raw_archives_dir)
        return root / release_name / relative_path

    @staticmethod
    def _source_domain_for_folder(source_name: str) -> str:
        return SOURCE_DOMAIN_MAP.get(
            source_name.lower(), f"{source_name.lower()}.local"
        )

    @staticmethod
    def _synthetic_url(release_name: str, relative_path: Path) -> str:
        relative_text = "/".join(quote(part) for part in relative_path.parts)
        return f"local-release://{quote(release_name)}/{relative_text}"
