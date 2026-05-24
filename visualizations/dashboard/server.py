from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

TEXT_SUFFIXES = {".txt", ".md", ".json", ".csv", ".xml", ".yaml", ".yml", ".log"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".tiff", ".tif", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
AUDIO_SUFFIXES = {".mp3", ".wav"}


@dataclass(slots=True)
class AppContext:
    project_root: Path
    db_path: Path
    dashboard_dir: Path


def _safe_relative_path(path: Path, base: Path) -> str:
    return str(path.resolve().relative_to(base.resolve())).replace("\\", "/")


def _preview_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return "text"
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in AUDIO_SUFFIXES:
        return "audio"
    if suffix == ".pdf":
        return "pdf"
    return "binary"


def _normalize_parse_status_from_meta(payload: dict[str, Any]) -> str:
    parse_status = str(payload.get("parse_status") or "").strip()
    if parse_status:
        return parse_status
    output_text_path = payload.get("output_text_path")
    return "parsed_legacy" if output_text_path else "unknown"


def _parse_status_from_meta_file(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return _normalize_parse_status_from_meta(payload)


def load_db_records(db_path: Path, project_root: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []

    records: list[dict[str, Any]] = []
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
        if "assets" not in tables:
            return []

        rows = connection.execute(
            """
            SELECT id, url, source_domain, asset_type, mime_type, size_bytes,
                   local_path, status, downloaded_at, discovered_at,
                   evidence_tier, evidence_rationale
            FROM assets
            ORDER BY id DESC
            """,
        ).fetchall()

        for row in rows:
            local_path = row["local_path"]
            abs_path: Path | None = None
            local_rel = ""
            if local_path:
                tentative = Path(local_path)
                abs_path = (
                    tentative if tentative.is_absolute() else project_root / tentative
                )
                if abs_path.exists():
                    local_rel = _safe_relative_path(abs_path, project_root)

            title = (
                local_rel.split("/")[-1]
                if local_rel
                else (row["url"] or f"asset-{row['id']}")
            )
            preview_type = _preview_type_for_path(abs_path) if abs_path else "external"

            records.append(
                {
                    "record_id": f"asset:{row['id']}",
                    "kind": "asset",
                    "title": title,
                    "url": row["url"],
                    "source_domain": row["source_domain"] or "",
                    "asset_type": row["asset_type"] or "",
                    "status": row["status"] or "",
                    "evidence_tier": row["evidence_tier"],
                    "evidence_rationale": row["evidence_rationale"] or "",
                    "mime_type": row["mime_type"] or "",
                    "size_bytes": row["size_bytes"],
                    "local_path": local_rel,
                    "preview_type": preview_type,
                    "downloaded_at": row["downloaded_at"],
                    "discovered_at": row["discovered_at"],
                },
            )

    return records


def load_file_records(project_root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    targets = [
        (project_root / "data" / "metadata", "metadata"),
        (project_root / "data" / "reports", "report"),
        (project_root / "data" / "parsed_text", "parsed_text"),
    ]

    for base_dir, kind in targets:
        if not base_dir.exists():
            continue

        for path in sorted(base_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = _safe_relative_path(path, project_root)
            preview_type = _preview_type_for_path(path)
            parse_status = ""
            if kind == "parsed_text":
                if path.name.endswith(".meta.json"):
                    parse_status = _parse_status_from_meta_file(path)
                else:
                    meta_candidate = path.with_suffix(".meta.json")
                    if meta_candidate.exists():
                        parse_status = _parse_status_from_meta_file(meta_candidate)
            records.append(
                {
                    "record_id": f"file:{rel}",
                    "kind": kind,
                    "title": path.name,
                    "url": "",
                    "source_domain": "",
                    "asset_type": kind,
                    "status": "indexed",
                    "parse_status": parse_status,
                    "evidence_tier": None,
                    "evidence_rationale": "",
                    "mime_type": mimetypes.guess_type(str(path))[0] or "",
                    "size_bytes": path.stat().st_size,
                    "local_path": rel,
                    "preview_type": preview_type,
                    "downloaded_at": None,
                    "discovered_at": None,
                },
            )

    return records


def all_records(ctx: AppContext) -> list[dict[str, Any]]:
    return load_db_records(ctx.db_path, ctx.project_root) + load_file_records(
        ctx.project_root
    )


def parse_status_summary(project_root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    parsed_root = project_root / "data" / "parsed_text"
    if not parsed_root.exists():
        return counts

    for sidecar_path in parsed_root.rglob("*.meta.json"):
        parse_status = _parse_status_from_meta_file(sidecar_path)
        if not parse_status:
            continue
        counts[parse_status] = counts.get(parse_status, 0) + 1

    return dict(sorted(counts.items(), key=lambda item: item[0]))


def top_entity_summary(db_path: Path, limit: int = 15) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []

    rows_out: list[dict[str, Any]] = []
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
        if "extracted_entities" not in tables:
            return []

        rows = connection.execute(
            """
            SELECT entity_type, normalized_text, SUM(occurrences) AS total_occurrences
            FROM extracted_entities
            GROUP BY entity_type, normalized_text
            ORDER BY total_occurrences DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    for row in rows:
        rows_out.append(
            {
                "entity_type": row["entity_type"],
                "normalized_text": row["normalized_text"],
                "total_occurrences": int(row["total_occurrences"]),
            }
        )
    return rows_out


def filter_records(
    records: list[dict[str, Any]], query: dict[str, list[str]]
) -> list[dict[str, Any]]:
    q = query.get("q", [""])[0].strip().lower()
    kind = query.get("kind", [""])[0].strip().lower()
    asset_type = query.get("asset_type", [""])[0].strip().lower()
    status = query.get("status", [""])[0].strip().lower()
    parse_status = query.get("parse_status", [""])[0].strip().lower()
    source_domain = query.get("source_domain", [""])[0].strip().lower()
    evidence_tier = query.get("evidence_tier", [""])[0].strip()
    discovered_from = query.get("discovered_from", [""])[0].strip()
    discovered_to = query.get("discovered_to", [""])[0].strip()

    filtered: list[dict[str, Any]] = []
    for record in records:
        if q:
            haystack = " ".join(
                [
                    str(record.get("title", "")),
                    str(record.get("url", "")),
                    str(record.get("local_path", "")),
                    str(record.get("source_domain", "")),
                    str(record.get("evidence_rationale", "")),
                ],
            ).lower()
            if q not in haystack:
                continue

        if kind and str(record.get("kind", "")).lower() != kind:
            continue
        if asset_type and str(record.get("asset_type", "")).lower() != asset_type:
            continue
        if status and str(record.get("status", "")).lower() != status:
            continue
        if parse_status and str(record.get("parse_status", "")).lower() != parse_status:
            continue
        if (
            source_domain
            and str(record.get("source_domain", "")).lower() != source_domain
        ):
            continue
        if evidence_tier:
            tier = record.get("evidence_tier")
            if tier is None or str(tier) != evidence_tier:
                continue

        discovered = str(record.get("discovered_at") or "")
        discovered_date = discovered[:10] if discovered else ""
        if discovered_from and discovered_date and discovered_date < discovered_from:
            continue
        if discovered_to and discovered_date and discovered_date > discovered_to:
            continue

        filtered.append(record)

    return filtered


def paginated(
    records: list[dict[str, Any]], query: dict[str, list[str]]
) -> tuple[list[dict[str, Any]], int, int]:
    page = max(1, int(query.get("page", ["1"])[0]))
    page_size = max(1, min(200, int(query.get("page_size", ["50"])[0])))
    start = (page - 1) * page_size
    end = start + page_size
    return records[start:end], page, page_size


def fetch_asset_findings(db_path: Path, asset_id: int) -> dict[str, Any]:
    """Return extracted entities and events for a single asset."""
    if not db_path.exists():
        return {"entities": [], "events": []}

    entities: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
        if "extracted_entities" in tables:
            rows = connection.execute(
                """
                SELECT entity_type, normalized_text, entity_text, confidence, occurrences
                FROM extracted_entities
                WHERE asset_id = ?
                ORDER BY occurrences DESC
                """,
                (asset_id,),
            ).fetchall()
            entities = [dict(r) for r in rows]

        if "extracted_events" in tables:
            rows = connection.execute(
                """
                SELECT event_type, event_date, event_text, confidence
                FROM extracted_events
                WHERE asset_id = ?
                ORDER BY confidence DESC
                """,
                (asset_id,),
            ).fetchall()
            events = [dict(r) for r in rows]

    return {"entities": entities, "events": events}


def preview_payload(ctx: AppContext, record_id: str) -> dict[str, Any]:
    if record_id.startswith("asset:"):
        asset_id = int(record_id.split(":", 1)[1])
        records = load_db_records(ctx.db_path, ctx.project_root)
        record = next(
            (r for r in records if r["record_id"] == f"asset:{asset_id}"), None
        )
        if record is None:
            return {"error": "Asset not found"}

        findings = fetch_asset_findings(ctx.db_path, asset_id)

        local_path = record.get("local_path") or ""
        if local_path:
            file_path = ctx.project_root / local_path
            payload = file_preview_payload(ctx, file_path, record)
            payload["findings"] = findings
            return payload

        return {
            "record": record,
            "findings": findings,
            "preview": {
                "type": "external",
                "external_url": record.get("url"),
            },
        }

    if record_id.startswith("file:"):
        rel = record_id.split(":", 1)[1]
        file_path = ctx.project_root / rel
        if not file_path.exists():
            return {"error": "File not found"}

        file_record = {
            "record_id": record_id,
            "local_path": rel,
            "title": file_path.name,
            "preview_type": _preview_type_for_path(file_path),
        }
        payload = file_preview_payload(ctx, file_path, file_record)
        payload["findings"] = {"entities": [], "events": []}
        return payload

    return {"error": "Unsupported record id"}


def file_preview_payload(
    ctx: AppContext, file_path: Path, record: dict[str, Any]
) -> dict[str, Any]:
    rel = _safe_relative_path(file_path, ctx.project_root)
    preview_type = record.get("preview_type") or _preview_type_for_path(file_path)
    payload: dict[str, Any] = {
        "record": record,
        "preview": {
            "type": preview_type,
            "media_url": f"/files/{rel}",
        },
    }

    if preview_type == "text":
        try:
            payload["preview"]["text"] = file_path.read_text(encoding="utf-8")[:20000]
        except UnicodeDecodeError:
            payload["preview"]["text"] = file_path.read_text(encoding="latin-1")[:20000]

    return payload


def entity_browse(
    db_path: Path, entity_type: str = "", limit: int = 50
) -> list[dict[str, Any]]:
    """Return top entities optionally filtered by type, with per-asset breakdown."""
    if not db_path.exists():
        return []

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
        if "extracted_entities" not in tables:
            return []

        if entity_type:
            rows = connection.execute(
                """
                SELECT ee.entity_type, ee.normalized_text, SUM(ee.occurrences) AS total,
                       GROUP_CONCAT(DISTINCT ee.asset_id) AS asset_ids
                FROM extracted_entities ee
                WHERE ee.entity_type = ?
                GROUP BY ee.entity_type, ee.normalized_text
                ORDER BY total DESC
                LIMIT ?
                """,
                (entity_type, limit),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT ee.entity_type, ee.normalized_text, SUM(ee.occurrences) AS total,
                       GROUP_CONCAT(DISTINCT ee.asset_id) AS asset_ids
                FROM extracted_entities ee
                GROUP BY ee.entity_type, ee.normalized_text
                ORDER BY total DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    return [
        {
            "entity_type": row["entity_type"],
            "normalized_text": row["normalized_text"],
            "total": int(row["total"]),
            "asset_ids": [int(x) for x in (row["asset_ids"] or "").split(",") if x],
        }
        for row in rows
    ]


def fetch_asset_debrief(ctx: AppContext, asset_id: int) -> dict[str, Any]:
    """Return a structured debrief for a single asset."""
    records = load_db_records(ctx.db_path, ctx.project_root)
    record = next(
        (r for r in records if int(r["record_id"].split(":", 1)[1]) == asset_id), None
    )
    if record is None:
        return {"error": "Asset not found"}

    findings = fetch_asset_findings(ctx.db_path, asset_id)
    entities = findings.get("entities", [])
    events = findings.get("events", [])

    top_entities = sorted(
        entities, key=lambda e: (-e.get("confidence", 0), -e.get("occurrences", 0))
    )[:10]
    sorted_events = sorted(events, key=lambda e: -e.get("confidence", 0))

    entity_count = len(entities)
    event_count = len(events)
    avg_entity_confidence = (
        sum(e.get("confidence", 0) for e in entities) / entity_count
        if entity_count > 0
        else 0
    )
    avg_event_confidence = (
        sum(e.get("confidence", 0) for e in events) / event_count
        if event_count > 0
        else 0
    )

    debrief = {
        "asset": record,
        "summary": {
            "total_entities": entity_count,
            "total_events": event_count,
            "avg_entity_confidence": round(avg_entity_confidence, 3),
            "avg_event_confidence": round(avg_event_confidence, 3),
            "confidence_tier": record.get("evidence_tier"),
        },
        "top_entities": top_entities,
        "all_events": sorted_events,
    }
    return debrief


def fetch_asset_correlations(
    ctx: AppContext, asset_id: int, correlation_type: str = "entity"
) -> list[dict[str, Any]]:
    """Return related assets by entity overlap, date, location, or domain."""
    if correlation_type not in {"entity", "date", "location", "domain"}:
        correlation_type = "entity"

    records = load_db_records(ctx.db_path, ctx.project_root)
    source_record = next(
        (r for r in records if int(r["record_id"].split(":", 1)[1]) == asset_id), None
    )
    if source_record is None:
        return []

    correlations: list[dict[str, Any]] = []

    if not ctx.db_path.exists():
        return correlations

    with sqlite3.connect(ctx.db_path) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
        if "extracted_entities" not in tables:
            return correlations

        if correlation_type == "entity":
            rows = connection.execute(
                """
                SELECT DISTINCT ee2.asset_id, COUNT(*) as shared_count
                FROM extracted_entities ee1
                JOIN extracted_entities ee2 ON ee1.normalized_text = ee2.normalized_text
                    AND ee1.entity_type = ee2.entity_type
                WHERE ee1.asset_id = ? AND ee2.asset_id != ?
                GROUP BY ee2.asset_id
                ORDER BY shared_count DESC
                """,
                (asset_id, asset_id),
            ).fetchall()

            for row in rows:
                linked_record = next(
                    (
                        r
                        for r in records
                        if int(r["record_id"].split(":", 1)[1]) == row["asset_id"]
                    ),
                    None,
                )
                if linked_record:
                    correlations.append(
                        {
                            "asset_id": row["asset_id"],
                            "title": linked_record.get("title"),
                            "why_linked": f"Shared {row['shared_count']} entities",
                            "link_strength": min(1.0, row["shared_count"] / 10.0),
                            "shared_count": row["shared_count"],
                        }
                    )

        elif correlation_type == "date":
            source_date = (
                source_record.get("discovered_at", "")[:10]
                if source_record.get("discovered_at")
                else None
            )
            if source_date:
                rows = connection.execute(
                    """
                    SELECT DISTINCT a.id, a.discovered_at
                    FROM assets a
                    WHERE a.id != ? AND a.discovered_at IS NOT NULL
                    ORDER BY ABS(julianday(a.discovered_at) - julianday(?))
                    LIMIT 20
                    """,
                    (asset_id, source_date),
                ).fetchall()

                for row in rows:
                    linked_record = next(
                        (
                            r
                            for r in records
                            if int(r["record_id"].split(":", 1)[1]) == row["id"]
                        ),
                        None,
                    )
                    if linked_record:
                        correlations.append(
                            {
                                "asset_id": row["id"],
                                "title": linked_record.get("title"),
                                "why_linked": f"Date proximity: {row['discovered_at'][:10]}",
                                "link_strength": 0.5,
                            }
                        )

        elif correlation_type == "domain":
            source_domain = source_record.get("source_domain", "")
            if source_domain:
                rows = connection.execute(
                    """
                    SELECT id FROM assets
                    WHERE id != ? AND source_domain = ?
                    """,
                    (asset_id, source_domain),
                ).fetchall()

                for row in rows:
                    linked_record = next(
                        (
                            r
                            for r in records
                            if int(r["record_id"].split(":", 1)[1]) == row["id"]
                        ),
                        None,
                    )
                    if linked_record:
                        correlations.append(
                            {
                                "asset_id": row["id"],
                                "title": linked_record.get("title"),
                                "why_linked": f"Same source: {source_domain}",
                                "link_strength": 0.7,
                            }
                        )

    return correlations


def fetch_timeline(
    ctx: AppContext, date_from: str = "", date_to: str = "", entity_filter: str = ""
) -> list[dict[str, Any]]:
    """Return chronological events with optional filtering."""
    if not ctx.db_path.exists():
        return []

    events: list[dict[str, Any]] = []
    with sqlite3.connect(ctx.db_path) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'",
            ).fetchall()
        }
        if "extracted_events" not in tables:
            return []

        query = "SELECT asset_id, event_type, event_date, event_text, confidence FROM extracted_events WHERE 1=1"
        params: list[Any] = []

        if date_from:
            query += " AND event_date >= ?"
            params.append(date_from)
        if date_to:
            query += " AND event_date <= ?"
            params.append(date_to)

        query += " ORDER BY event_date DESC, confidence DESC"

        rows = connection.execute(query, params).fetchall()

        records = load_db_records(ctx.db_path, ctx.project_root)
        for row in rows:
            asset_record = next(
                (
                    r
                    for r in records
                    if int(r["record_id"].split(":", 1)[1]) == row["asset_id"]
                ),
                None,
            )
            events.append(
                {
                    "asset_id": row["asset_id"],
                    "asset_title": (
                        asset_record.get("title")
                        if asset_record
                        else f"Asset {row['asset_id']}"
                    ),
                    "event_type": row["event_type"],
                    "event_date": row["event_date"],
                    "event_text": row["event_text"][:200] if row["event_text"] else "",
                    "confidence": row["confidence"],
                }
            )

    return events


class DashboardHandler(BaseHTTPRequestHandler):
    context: AppContext

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)

        if parsed.path in {"/", "/index.html", "/app.js", "/styles.css"}:
            self._serve_dashboard_file(parsed.path)
            return

        if parsed.path.startswith("/api/records"):
            self._handle_api_records(parsed)
            return

        if parsed.path.startswith("/api/summary"):
            self._handle_api_summary()
            return

        if parsed.path.startswith("/api/insights"):
            self._handle_api_insights()
            return

        if parsed.path.startswith("/api/entities"):
            self._handle_api_entities(parsed)
            return

        if parsed.path.startswith("/api/preview"):
            self._handle_api_preview(parsed)
            return

        if parsed.path.startswith("/api/debrief"):
            self._handle_api_debrief(parsed)
            return

        if parsed.path.startswith("/api/correlations"):
            self._handle_api_correlations(parsed)
            return

        if parsed.path.startswith("/api/timeline"):
            self._handle_api_timeline(parsed)
            return

        if parsed.path.startswith("/files/"):
            self._serve_project_file(parsed.path)
            return

        self._json({"error": "Not found"}, status=404)

    def _serve_dashboard_file(self, route_path: str) -> None:
        name = (
            "index.html"
            if route_path in {"/", "/index.html"}
            else route_path.lstrip("/")
        )
        file_path = self.context.dashboard_dir / name
        if not file_path.exists():
            self._json({"error": "Dashboard file missing"}, status=404)
            return

        mime = "text/html"
        if name.endswith(".js"):
            mime = "application/javascript"
        if name.endswith(".css"):
            mime = "text/css"

        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_project_file(self, route_path: str) -> None:
        rel = route_path.split("/files/", 1)[1]
        rel_path = Path(rel)
        file_path = (self.context.project_root / rel_path).resolve()
        allowed_roots = {
            (self.context.project_root / "data").resolve(),
            (self.context.project_root / "direct_download").resolve(),
        }

        if not any(
            file_path == root or root in file_path.parents for root in allowed_roots
        ):
            self._json({"error": "Forbidden"}, status=403)
            return
        if not file_path.exists() or not file_path.is_file():
            self._json({"error": "File not found"}, status=404)
            return

        body = file_path.read_bytes()
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_api_records(self, parsed) -> None:
        query = parse_qs(parsed.query)
        records = all_records(self.context)
        records = filter_records(records, query)
        total = len(records)
        items, page, page_size = paginated(records, query)
        self._json(
            {
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size,
            },
        )

    def _handle_api_summary(self) -> None:
        records = all_records(self.context)
        kinds = sorted({r["kind"] for r in records if r.get("kind")})
        asset_types = sorted({r["asset_type"] for r in records if r.get("asset_type")})
        statuses = sorted({r["status"] for r in records if r.get("status")})
        domains = sorted(
            {r["source_domain"] for r in records if r.get("source_domain")}
        )
        tiers = sorted(
            {
                int(r["evidence_tier"])
                for r in records
                if r.get("evidence_tier") is not None
            },
        )

        self._json(
            {
                "total_records": len(records),
                "kinds": kinds,
                "asset_types": asset_types,
                "statuses": statuses,
                "source_domains": domains,
                "evidence_tiers": tiers,
            },
        )

    def _handle_api_preview(self, parsed) -> None:
        query = parse_qs(parsed.query)
        record_id = query.get("record_id", [""])[0]
        if not record_id:
            self._json({"error": "record_id is required"}, status=400)
            return

        self._json(preview_payload(self.context, record_id))

    def _handle_api_insights(self) -> None:
        self._json(
            {
                "parse_status": parse_status_summary(self.context.project_root),
                "top_entities": top_entity_summary(self.context.db_path, limit=15),
            },
        )

    def _handle_api_entities(self, parsed) -> None:
        query = parse_qs(parsed.query)
        entity_type = query.get("entity_type", [""])[0].strip()
        limit = min(200, max(1, int(query.get("limit", ["100"])[0])))
        self._json(
            {
                "entities": entity_browse(
                    self.context.db_path, entity_type=entity_type, limit=limit
                )
            }
        )

    def _handle_api_debrief(self, parsed) -> None:
        query = parse_qs(parsed.query)
        asset_id_str = query.get("asset_id", [""])[0]
        if not asset_id_str:
            self._json({"error": "asset_id is required"}, status=400)
            return
        try:
            asset_id = int(asset_id_str)
        except ValueError:
            self._json({"error": "asset_id must be an integer"}, status=400)
            return

        debrief = fetch_asset_debrief(self.context, asset_id)
        self._json(debrief)

    def _handle_api_correlations(self, parsed) -> None:
        query = parse_qs(parsed.query)
        asset_id_str = query.get("asset_id", [""])[0]
        correlation_type = query.get("type", ["entity"])[0].strip()
        if not asset_id_str:
            self._json({"error": "asset_id is required"}, status=400)
            return
        try:
            asset_id = int(asset_id_str)
        except ValueError:
            self._json({"error": "asset_id must be an integer"}, status=400)
            return

        correlations = fetch_asset_correlations(
            self.context, asset_id, correlation_type
        )
        self._json({"correlations": correlations, "type": correlation_type})

    def _handle_api_timeline(self, parsed) -> None:
        query = parse_qs(parsed.query)
        date_from = query.get("date_from", [""])[0].strip()
        date_to = query.get("date_to", [""])[0].strip()
        entity_filter = query.get("entity_filter", [""])[0].strip()

        timeline = fetch_timeline(self.context, date_from, date_to, entity_filter)
        self._json({"events": timeline})

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def build_handler(context: AppContext):
    class _Handler(DashboardHandler):
        pass

    _Handler.context = context
    return _Handler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Local dashboard server for ProjectHiddenThreads",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18117)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dashboard_dir = Path(__file__).resolve().parent
    project_root = dashboard_dir.parents[1]
    context = AppContext(
        project_root=project_root,
        db_path=project_root / "database" / "archive.db",
        dashboard_dir=dashboard_dir,
    )

    server = ThreadingHTTPServer((args.host, args.port), build_handler(context))
    print(f"Dashboard running at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
