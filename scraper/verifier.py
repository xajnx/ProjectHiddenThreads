from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from database.db import Database
from scraper.settings import AppConfig

LOGGER = logging.getLogger(__name__)

BLOCKED_STATUSES = {401, 403}


@dataclass(slots=True)
class VerificationResult:
    new: int = 0
    unchanged: int = 0
    modified: int = 0
    blocked: int = 0
    unknown: int = 0


class AssetVerifier:
    """Verification phase for classifying assets before acquisition attempts."""

    def __init__(self, db: Database, config: AppConfig) -> None:
        self.db = db
        self.config = config
        self.session = self._build_session(
            retries=config.download.retries,
            backoff_factor=config.download.backoff_factor,
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
            allowed_methods=frozenset({"HEAD", "GET"}),
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update({"User-Agent": "ProjectHiddenThreadsBot/0.1"})
        return session

    def run(self, crawl_session_id: int) -> VerificationResult:
        assets = self.db.list_assets_for_verification()
        result = VerificationResult()

        for asset in assets:
            asset_id = int(asset["id"])
            url = str(asset["url"])

            try:
                response = self.session.head(
                    url,
                    allow_redirects=True,
                    timeout=self.config.crawl.request_timeout_seconds,
                )
                status_code = response.status_code
                etag = response.headers.get("ETag")
                last_modified = response.headers.get("Last-Modified")
                content_length_raw = response.headers.get("Content-Length")
                content_length = (
                    int(content_length_raw)
                    if content_length_raw and content_length_raw.isdigit()
                    else None
                )
                mime_type = (
                    response.headers.get("Content-Type", "").split(";", 1)[0] or None
                )

                if status_code in BLOCKED_STATUSES:
                    self.db.update_asset_verification(
                        asset_id,
                        verification_state="blocked",
                        status="blocked",
                        etag=etag,
                        last_modified=last_modified,
                        content_length=content_length,
                        mime_type=mime_type,
                        download_status="blocked",
                    )
                    result.blocked += 1
                else:
                    classification = self._classify(
                        asset, etag, last_modified, content_length
                    )
                    self.db.update_asset_verification(
                        asset_id,
                        verification_state=classification,
                        status=classification,
                        etag=etag,
                        last_modified=last_modified,
                        content_length=content_length,
                        mime_type=mime_type,
                        download_status=(
                            "new" if classification in {"new", "modified"} else None
                        ),
                    )
                    if classification == "new":
                        result.new += 1
                    elif classification == "unchanged":
                        result.unchanged += 1
                    elif classification == "modified":
                        result.modified += 1
                    else:
                        result.unknown += 1

                self.db.append_ledger_event(
                    event_type="verification",
                    actor="verifier",
                    payload={
                        "url": url,
                        "status_code": status_code,
                    },
                    crawl_session_id=crawl_session_id,
                    asset_id=asset_id,
                )
            except requests.RequestException as exc:
                self.db.update_asset_verification(
                    asset_id,
                    verification_state="unknown",
                    status="unknown",
                    etag=asset["etag"],
                    last_modified=asset["last_modified"],
                    content_length=asset["content_length"],
                    mime_type=asset["mime_type"],
                )
                result.unknown += 1
                self.db.append_ledger_event(
                    event_type="verification",
                    actor="verifier",
                    payload={"url": url, "error": str(exc), "status": "unknown"},
                    crawl_session_id=crawl_session_id,
                    asset_id=asset_id,
                )
                LOGGER.warning(
                    "Verification failed", extra={"url": url, "error": str(exc)}
                )

        return result

    @staticmethod
    def _classify(
        asset,
        etag: str | None,
        last_modified: str | None,
        content_length: int | None,
    ) -> str:
        if not asset["downloaded_at"]:
            return "new"

        if etag and asset["etag"] and etag == asset["etag"]:
            if (
                last_modified
                and asset["last_modified"]
                and last_modified == asset["last_modified"]
            ):
                if content_length is None or asset["content_length"] is None:
                    return "unchanged"
                if int(asset["content_length"]) == int(content_length):
                    return "unchanged"

        if (
            last_modified
            and asset["last_modified"]
            and last_modified != asset["last_modified"]
        ):
            return "modified"

        if content_length is not None and asset["content_length"] is not None:
            if int(asset["content_length"]) != int(content_length):
                return "modified"

        if etag and asset["etag"] and etag != asset["etag"]:
            return "modified"

        return "unknown"
