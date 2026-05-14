from __future__ import annotations

import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from database.db import Database
from scraper.asset_classifier import (
    ASSET_TYPE_ARCHIVE,
    ASSET_TYPE_AUDIO,
    ASSET_TYPE_DOCUMENT,
    ASSET_TYPE_IMAGE,
    ASSET_TYPE_VIDEO,
)
from scraper.checksum import sha256_file
from scraper.settings import AppConfig
from scraper.version_tracker import VersionTracker

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class DownloadResult:
    downloaded: int = 0
    skipped_unchanged: int = 0
    blocked: int = 0
    failed: int = 0


class AssetDownloader:
    def __init__(self, db: Database, config: AppConfig) -> None:
        self.db = db
        self.config = config
        self.version_tracker = VersionTracker(config.data.versions_dir)
        self.session = self._build_session(
            config.download.retries, config.download.backoff_factor
        )

    @staticmethod
    def _build_session(retries: int, backoff_factor: float) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=retries,
            read=retries,
            connect=retries,
            backoff_factor=backoff_factor,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update({"User-Agent": "ProjectHiddenThreadsBot/0.1"})
        return session

    def run_downloads(self, crawl_session_id: int) -> DownloadResult:
        assets = self.db.list_assets_for_acquisition()
        if not assets:
            return DownloadResult()

        result = DownloadResult()
        with ThreadPoolExecutor(
            max_workers=self.config.download.max_workers
        ) as executor:
            futures = [
                executor.submit(self._process_asset, crawl_session_id, asset)
                for asset in assets
            ]

            for future in as_completed(futures):
                status = future.result()
                if status == "downloaded":
                    result.downloaded += 1
                elif status == "skipped":
                    result.skipped_unchanged += 1
                elif status == "blocked":
                    result.blocked += 1
                else:
                    result.failed += 1

        return result

    def _process_asset(self, crawl_session_id: int, asset: sqlite3.Row) -> str:
        asset_id = int(asset["id"])
        url = asset["url"]

        try:
            metadata = self._head_metadata(url)
            if metadata.get("blocked"):
                self.db.update_asset_status(asset_id, "blocked")
                self.db.update_asset_verification(
                    asset_id,
                    verification_state="blocked",
                    status="blocked",
                    etag=metadata.get("etag"),
                    last_modified=metadata.get("last_modified"),
                    content_length=metadata.get("content_length_int"),
                    mime_type=metadata.get("mime_type"),
                    download_status="blocked",
                )
                self.db.append_ledger_event(
                    event_type="download",
                    actor="downloader",
                    payload={
                        "url": url,
                        "status": "blocked",
                        "reason": "access_control",
                    },
                    crawl_session_id=crawl_session_id,
                    asset_id=asset_id,
                )
                return "blocked"

            if self._is_unchanged(asset, metadata):
                self.db.update_asset_status(asset_id, "unchanged")
                self.db.append_ledger_event(
                    event_type="modification_detection",
                    actor="downloader",
                    payload={"reason": "etag_last_modified_match", "url": url},
                    crawl_session_id=crawl_session_id,
                    asset_id=asset_id,
                )
                return "skipped"

            target_path = self._target_path(asset, url)
            target_path.parent.mkdir(parents=True, exist_ok=True)

            if asset["local_path"]:
                current_path = Path(asset["local_path"])
                if current_path.exists():
                    archived = self.version_tracker.archive_existing_file(
                        current_path, asset_id
                    )
                    self.db.add_asset_version(
                        asset_id,
                        previous_sha256=asset["sha256"],
                        previous_path=asset["local_path"],
                        archived_path=str(archived),
                        change_reason="upstream_changed",
                    )

            download_outcome = self._download_file(url, target_path)
            if download_outcome == "blocked":
                self.db.update_asset_status(asset_id, "blocked")
                self.db.update_asset_verification(
                    asset_id,
                    verification_state="blocked",
                    status="blocked",
                    etag=metadata.get("etag"),
                    last_modified=metadata.get("last_modified"),
                    content_length=metadata.get("content_length_int"),
                    mime_type=metadata.get("mime_type"),
                    download_status="blocked",
                )
                return "blocked"

            checksum = sha256_file(target_path)
            size_bytes = target_path.stat().st_size

            if asset["sha256"] and asset["sha256"] == checksum:
                self.db.update_asset_status(asset_id, "unchanged")
                self.db.append_ledger_event(
                    event_type="modification_detection",
                    actor="downloader",
                    payload={"reason": "sha256_match", "url": url},
                    crawl_session_id=crawl_session_id,
                    asset_id=asset_id,
                )
                return "skipped"

            self.db.record_asset_download(
                asset_id,
                sha256=checksum,
                size_bytes=size_bytes,
                local_path=str(target_path),
                mime_type=metadata.get("mime_type"),
                etag=metadata.get("etag"),
                last_modified=metadata.get("last_modified"),
                status="downloaded",
            )
            self.db.append_ledger_event(
                event_type="download",
                actor="downloader",
                payload={"url": url, "sha256": checksum, "size_bytes": size_bytes},
                crawl_session_id=crawl_session_id,
                asset_id=asset_id,
            )
            return "downloaded"

        except Exception as exc:
            self.db.update_asset_status(asset_id, "download_failed")
            self.db.append_ledger_event(
                event_type="download",
                actor="downloader",
                payload={"url": url, "status": "failed", "error": str(exc)},
                crawl_session_id=crawl_session_id,
                asset_id=asset_id,
            )
            LOGGER.exception(
                "Download failed", extra={"asset_id": asset_id, "url": url}
            )
            return "failed"

    def _head_metadata(self, url: str) -> dict[str, Any]:
        response = self.session.head(url, allow_redirects=True, timeout=20)
        blocked = response.status_code in {401, 403}
        if response.status_code >= 400 and not blocked:
            response.raise_for_status()

        content_length_raw = response.headers.get("Content-Length")
        content_length_int = (
            int(content_length_raw)
            if content_length_raw and content_length_raw.isdigit()
            else None
        )

        return {
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "content_length": content_length_raw,
            "content_length_int": content_length_int,
            "mime_type": response.headers.get("Content-Type", "").split(";", 1)[0]
            or None,
            "blocked": blocked,
        }

    @staticmethod
    def _is_unchanged(asset: sqlite3.Row, metadata: dict[str, str | None]) -> bool:
        etag = metadata.get("etag")
        last_modified = metadata.get("last_modified")
        content_length = metadata.get("content_length")

        if not asset["etag"] or not asset["last_modified"]:
            return False

        if etag and last_modified:
            if asset["etag"] == etag and asset["last_modified"] == last_modified:
                if (
                    asset["size_bytes"]
                    and content_length
                    and str(asset["size_bytes"]) == str(content_length)
                ):
                    return True

        return False

    def _target_path(self, asset: sqlite3.Row, url: str) -> Path:
        asset_type = asset["asset_type"]
        suffix = Path(urlparse(url).path).suffix.lower()
        filename = f"asset_{asset['id']}{suffix or '.bin'}"

        root_map = {
            ASSET_TYPE_DOCUMENT: self.config.data.raw_documents_dir,
            ASSET_TYPE_VIDEO: self.config.data.raw_videos_dir,
            ASSET_TYPE_AUDIO: self.config.data.raw_audio_dir,
            ASSET_TYPE_IMAGE: self.config.data.raw_images_dir,
            ASSET_TYPE_ARCHIVE: self.config.data.raw_archives_dir,
        }
        root = root_map.get(asset_type, self.config.data.raw_archives_dir)
        return root / filename

    def _download_file(self, url: str, destination: Path) -> str:
        resume_from = destination.stat().st_size if destination.exists() else 0
        headers: dict[str, str] = {}
        mode = "wb"

        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"
            mode = "ab"

        with self.session.get(
            url, headers=headers, stream=True, timeout=60
        ) as response:
            if response.status_code == 416:
                return "downloaded"
            if response.status_code in {401, 403}:
                return "blocked"
            response.raise_for_status()

            if response.status_code == 200 and mode == "ab":
                mode = "wb"

            with destination.open(mode) as handle:
                for chunk in response.iter_content(
                    chunk_size=self.config.download.chunk_size_bytes
                ):
                    if chunk:
                        handle.write(chunk)
        return "downloaded"
