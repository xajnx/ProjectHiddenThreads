from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Optional

from scraper.models import DiscoveredAsset


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Database:
    """SQLite adapter for crawl, asset, and evidence-ledger workflows."""

    def __init__(self, db_path: Path, schema_path: Path) -> None:
        self.db_path = db_path
        self.schema_path = schema_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        schema_sql = self.schema_path.read_text(encoding="utf-8")
        with self.connect() as connection:
            connection.executescript(schema_sql)
            self._ensure_schema_compatibility(connection)

    @staticmethod
    def _ensure_schema_compatibility(connection: sqlite3.Connection) -> None:
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(assets)").fetchall()
        }
        if "evidence_tier" not in columns:
            connection.execute(
                "ALTER TABLE assets ADD COLUMN evidence_tier INTEGER NOT NULL DEFAULT 3",
            )
        if "evidence_rationale" not in columns:
            connection.execute("ALTER TABLE assets ADD COLUMN evidence_rationale TEXT")
        if "first_seen_timestamp" not in columns:
            connection.execute(
                "ALTER TABLE assets ADD COLUMN first_seen_timestamp TEXT"
            )
        if "last_seen_timestamp" not in columns:
            connection.execute("ALTER TABLE assets ADD COLUMN last_seen_timestamp TEXT")
        if "download_status" not in columns:
            connection.execute(
                "ALTER TABLE assets ADD COLUMN download_status TEXT NOT NULL DEFAULT 'new'",
            )
        if "verification_state" not in columns:
            connection.execute(
                "ALTER TABLE assets ADD COLUMN verification_state TEXT NOT NULL DEFAULT 'unknown'",
            )
        if "file_hash" not in columns:
            connection.execute("ALTER TABLE assets ADD COLUMN file_hash TEXT")
        if "content_length" not in columns:
            connection.execute("ALTER TABLE assets ADD COLUMN content_length INTEGER")
        if "parent_page_url" not in columns:
            connection.execute("ALTER TABLE assets ADD COLUMN parent_page_url TEXT")

        session_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(crawl_sessions)"
            ).fetchall()
        }
        if "assets_downloaded" not in session_columns:
            connection.execute(
                "ALTER TABLE crawl_sessions ADD COLUMN assets_downloaded INTEGER NOT NULL DEFAULT 0",
            )
        if "assets_blocked" not in session_columns:
            connection.execute(
                "ALTER TABLE crawl_sessions ADD COLUMN assets_blocked INTEGER NOT NULL DEFAULT 0",
            )

    @staticmethod
    def _cursor_lastrowid(cursor: sqlite3.Cursor) -> int:
        if cursor.lastrowid is None:
            raise RuntimeError("Database cursor did not return lastrowid")
        return int(cursor.lastrowid)

    def start_crawl_session(self, seed_domains: list[str]) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO crawl_sessions (started_at, seed_domains, status)
                VALUES (?, ?, ?)
                """,
                (utc_now(), json.dumps(seed_domains), "running"),
            )
            return self._cursor_lastrowid(cursor)

    def finish_crawl_session(
        self,
        crawl_session_id: int,
        pages_crawled: int,
        assets_discovered: int,
        assets_downloaded: int,
        assets_blocked: int,
        status: str,
        notes: Optional[str] = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE crawl_sessions
                SET ended_at = ?, status = ?, pages_crawled = ?, assets_discovered = ?,
                    assets_downloaded = ?, assets_blocked = ?, notes = ?
                WHERE id = ?
                """,
                (
                    utc_now(),
                    status,
                    pages_crawled,
                    assets_discovered,
                    assets_downloaded,
                    assets_blocked,
                    notes,
                    crawl_session_id,
                ),
            )

    def upsert_source_domain(self, domain: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO source_domains (domain)
                VALUES (?)
                ON CONFLICT(domain) DO NOTHING
                """,
                (domain,),
            )

    def get_asset_by_url(self, url: str) -> Optional[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM assets WHERE url = ?",
                (url,),
            ).fetchone()

    def upsert_discovered_asset(self, asset: DiscoveredAsset) -> int:
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id FROM assets WHERE url = ?",
                (asset.url,),
            ).fetchone()

            if existing:
                connection.execute(
                    """
                    UPDATE assets
                    SET source_domain = ?, asset_type = ?, mime_type = ?,
                        last_seen_timestamp = ?,
                        parent_page = ?, evidence_tier = ?, evidence_rationale = ?,
                        parent_page_url = ?, verification_state = 'unknown',
                        status = 'discovered', updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        asset.source_domain,
                        asset.asset_type,
                        asset.mime_type,
                        utc_now(),
                        asset.parent_page,
                        asset.evidence_tier,
                        asset.evidence_rationale,
                        asset.parent_page,
                        utc_now(),
                        existing["id"],
                    ),
                )
                self.add_evidence_grade(
                    asset_id=int(existing["id"]),
                    tier=asset.evidence_tier,
                    rationale=asset.evidence_rationale,
                    method="rule_based_v1",
                    connection=connection,
                )
                return int(existing["id"])

            cursor = connection.execute(
                """
                INSERT INTO assets (
                    url, source_domain, asset_type, mime_type,
                    first_seen_timestamp, last_seen_timestamp,
                    discovered_at, parent_page, parent_page_url,
                    evidence_tier, evidence_rationale,
                    verification_state, download_status, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset.url,
                    asset.source_domain,
                    asset.asset_type,
                    asset.mime_type,
                    utc_now(),
                    utc_now(),
                    asset.discovered_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    asset.parent_page,
                    asset.parent_page,  # parent_page_url
                    asset.evidence_tier,
                    asset.evidence_rationale,
                    "unknown",
                    "new",
                    "discovered",
                ),
            )
            created_id = self._cursor_lastrowid(cursor)
            self.add_evidence_grade(
                asset_id=created_id,
                tier=asset.evidence_tier,
                rationale=asset.evidence_rationale,
                method="rule_based_v1",
                connection=connection,
            )
            return created_id

    def add_evidence_grade(
        self,
        asset_id: int,
        tier: int,
        rationale: str,
        method: str,
        connection: Optional[sqlite3.Connection] = None,
    ) -> None:
        statement = """
            INSERT INTO evidence_grades (asset_id, tier, rationale, method, graded_at)
            VALUES (?, ?, ?, ?, ?)
            """
        args = (asset_id, tier, rationale, method, utc_now())

        if connection is not None:
            connection.execute(statement, args)
            return

        with self.connect() as conn:
            conn.execute(statement, args)

    def list_assets_for_download(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            rows = connection.execute("""
                SELECT *
                FROM assets
                WHERE status IN ('discovered', 'changed', 'download_failed')
                ORDER BY discovered_at ASC
                """).fetchall()
            return rows

    def list_assets_for_verification(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM assets
                WHERE status IN ('discovered', 'downloaded', 'unchanged', 'changed', 'blocked', 'download_failed')
                ORDER BY last_seen_timestamp DESC, discovered_at DESC
                """,
            ).fetchall()

    def list_assets_for_acquisition(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM assets
                WHERE verification_state IN ('new', 'modified')
                  AND download_status != 'blocked'
                  AND status != 'blocked'
                ORDER BY discovered_at ASC
                """,
            ).fetchall()

    def list_assets_for_parsing(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM assets
                WHERE status = 'downloaded'
                  AND asset_type = 'document'
                  AND local_path IS NOT NULL
                ORDER BY id ASC
                """,
            ).fetchall()

    def list_assets_for_image_parsing(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM assets
                WHERE status = 'downloaded'
                  AND asset_type = 'image'
                  AND local_path IS NOT NULL
                ORDER BY id ASC
                """,
            ).fetchall()

    def update_asset_verification(
        self,
        asset_id: int,
        *,
        verification_state: str,
        status: str,
        etag: Optional[str],
        last_modified: Optional[str],
        content_length: Optional[int],
        mime_type: Optional[str],
        download_status: Optional[str] = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE assets
                SET verification_state = ?, status = ?, etag = ?, last_modified = ?,
                    content_length = ?, mime_type = COALESCE(?, mime_type),
                    download_status = COALESCE(?, download_status),
                    last_seen_timestamp = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    verification_state,
                    status,
                    etag,
                    last_modified,
                    content_length,
                    mime_type,
                    download_status,
                    utc_now(),
                    utc_now(),
                    asset_id,
                ),
            )

    def record_asset_download(
        self,
        asset_id: int,
        *,
        sha256: str,
        size_bytes: int,
        local_path: str,
        mime_type: Optional[str],
        etag: Optional[str],
        last_modified: Optional[str],
        status: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE assets
                SET sha256 = ?, size_bytes = ?, local_path = ?, mime_type = ?,
                    file_hash = ?, content_length = ?, etag = ?, last_modified = ?,
                    downloaded_at = ?, download_status = 'downloaded',
                    verification_state = 'unchanged', status = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    sha256,
                    size_bytes,
                    local_path,
                    mime_type,
                    sha256,
                    size_bytes,
                    etag,
                    last_modified,
                    utc_now(),
                    status,
                    utc_now(),
                    asset_id,
                ),
            )

    def update_asset_status(self, asset_id: int, status: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE assets
                SET status = ?,
                    download_status = CASE
                        WHEN ? = 'blocked' THEN 'blocked'
                        WHEN ? = 'downloaded' THEN 'downloaded'
                        WHEN ? = 'unchanged' THEN 'skipped'
                        ELSE download_status
                    END,
                    updated_at = ?
                WHERE id = ?
                """,
                (status, status, status, status, utc_now(), asset_id),
            )

    def mark_asset_processed(self, asset_id: int, processed: bool) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE assets
                SET processed = ?, updated_at = ?
                WHERE id = ?
                """,
                (1 if processed else 0, utc_now(), asset_id),
            )

    def list_all_assets(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM assets ORDER BY id DESC",
            ).fetchall()

    def summarize_asset_status_counts(self) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM assets
                GROUP BY status
                """,
            ).fetchall()
            return {str(row["status"]): int(row["count"]) for row in rows}

    def add_asset_version(
        self,
        asset_id: int,
        previous_sha256: Optional[str],
        previous_path: Optional[str],
        archived_path: str,
        change_reason: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO asset_versions (
                    asset_id, previous_sha256, previous_path, archived_path, archived_at, change_reason
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    previous_sha256,
                    previous_path,
                    archived_path,
                    utc_now(),
                    change_reason,
                ),
            )

    def append_ledger_event(
        self,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        *,
        crawl_session_id: Optional[int] = None,
        asset_id: Optional[int] = None,
    ) -> None:
        payload_json = json.dumps(payload, sort_keys=True)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO evidence_ledger (
                    crawl_session_id, asset_id, event_type,
                    event_time, actor, event_payload, payload_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    crawl_session_id,
                    asset_id,
                    event_type,
                    utc_now(),
                    actor,
                    payload_json,
                    payload_sha256,
                ),
            )

    def add_extraction_log(
        self,
        *,
        asset_id: int,
        module_name: str,
        event_type: str,
        status: str,
        detail: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO extraction_logs (
                    asset_id, module_name, event_type, status, detail, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (asset_id, module_name, event_type, status, detail, utc_now()),
            )

    def replace_extractions_for_asset(
        self,
        *,
        asset_id: int,
        source_sha256: str,
        extractor_version: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                DELETE FROM extracted_entities
                WHERE asset_id = ? AND source_sha256 = ? AND extractor_version = ?
                """,
                (asset_id, source_sha256, extractor_version),
            )
            connection.execute(
                """
                DELETE FROM extracted_events
                WHERE asset_id = ? AND source_sha256 = ? AND extractor_version = ?
                """,
                (asset_id, source_sha256, extractor_version),
            )

    def add_extracted_entity(
        self,
        *,
        asset_id: int,
        source_sha256: str,
        extractor_version: str,
        entity_type: str,
        entity_text: str,
        normalized_text: str,
        confidence: float,
        occurrences: int,
        contexts_json: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO extracted_entities (
                    asset_id, source_sha256, extractor_version, entity_type,
                    entity_text, normalized_text, confidence, occurrences,
                    contexts_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    source_sha256,
                    extractor_version,
                    entity_type,
                    entity_text,
                    normalized_text,
                    confidence,
                    occurrences,
                    contexts_json,
                    utc_now(),
                ),
            )

    def add_extracted_event(
        self,
        *,
        asset_id: int,
        source_sha256: str,
        extractor_version: str,
        event_type: str,
        event_text: str,
        event_date: str | None,
        confidence: float,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO extracted_events (
                    asset_id, source_sha256, extractor_version, event_type,
                    event_text, event_date, confidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    source_sha256,
                    extractor_version,
                    event_type,
                    event_text,
                    event_date,
                    confidence,
                    utc_now(),
                ),
            )

    def top_extracted_entities(self, limit: int = 25) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT entity_type, normalized_text, SUM(occurrences) AS total_occurrences
                FROM extracted_entities
                GROUP BY entity_type, normalized_text
                ORDER BY total_occurrences DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def append_discovery_event(
        self,
        crawl_session_id: int,
        asset_id: int,
        asset: DiscoveredAsset,
    ) -> None:
        payload = asdict(asset)
        payload["discovered_at"] = asset.discovered_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.append_ledger_event(
            event_type="discovery",
            actor="crawler",
            payload=payload,
            crawl_session_id=crawl_session_id,
            asset_id=asset_id,
        )
