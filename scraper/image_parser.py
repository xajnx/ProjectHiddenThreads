from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from database.db import Database

try:
    import pytesseract
    from PIL import Image
except ImportError:  # pragma: no cover - exercised only when dependency is missing.
    pytesseract = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment]

LOGGER = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp"}


@dataclass(slots=True)
class ImageParseResult:
    parsed: int = 0
    skipped: int = 0
    failed: int = 0
    unsupported: int = 0


class ImageParser:
    def __init__(self, db: Database, project_root: Path) -> None:
        self.db = db
        self.project_root = project_root
        self.parsed_root = project_root / "data" / "parsed_text"

    def run(self, crawl_session_id: int) -> ImageParseResult:
        result = ImageParseResult()
        for asset in self.db.list_assets_for_image_parsing():
            status = self._process_asset(asset, crawl_session_id)
            if status == "parsed":
                result.parsed += 1
            elif status == "skipped":
                result.skipped += 1
            elif status == "unsupported":
                result.unsupported += 1
            else:
                result.failed += 1
        return result

    def _process_asset(self, asset, crawl_session_id: int) -> str:
        asset_id = int(asset["id"])
        local_path = Path(asset["local_path"])
        suffix = local_path.suffix.lower()

        if suffix not in SUPPORTED_SUFFIXES:
            self.db.add_extraction_log(
                asset_id=asset_id,
                module_name="image_parser",
                event_type="parse",
                status="unsupported",
                detail=f"Unsupported image suffix: {suffix or '<none>'}",
            )
            return "unsupported"

        if not local_path.exists():
            detail = f"Local image missing: {local_path}"
            self.db.add_extraction_log(
                asset_id=asset_id,
                module_name="image_parser",
                event_type="parse",
                status="failed",
                detail=detail,
            )
            self.db.mark_asset_processed(asset_id, False)
            return "failed"

        text_output_path, metadata_output_path = self._output_paths(local_path)
        if self._is_current(asset["sha256"], text_output_path, metadata_output_path):
            self.db.mark_asset_processed(asset_id, True)
            self.db.add_extraction_log(
                asset_id=asset_id,
                module_name="image_parser",
                event_type="parse",
                status="skipped",
                detail=f"Image parse output is current: {metadata_output_path}",
            )
            return "skipped"

        try:
            text, image_meta = self._extract_ocr_and_metadata(local_path)

            text_output_path.parent.mkdir(parents=True, exist_ok=True)
            parse_status = "parsed_ocr_image"
            output_text_path: str | None = str(text_output_path)
            characters = len(text)

            if text:
                text_output_path.write_text(text, encoding="utf-8")
            else:
                parse_status = "visual_only_no_text"
                output_text_path = None
                characters = 0
                if text_output_path.exists():
                    text_output_path.unlink()

            metadata = {
                "asset_id": asset_id,
                "source_url": asset["url"],
                "source_path": str(local_path),
                "source_sha256": asset["sha256"],
                "source_mime_type": asset["mime_type"],
                "parser": "pytesseract+pil",
                "parsed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "characters": characters,
                "output_text_path": output_text_path,
                "parse_status": parse_status,
                "image": image_meta,
            }
            metadata_output_path.write_text(
                json.dumps(metadata, indent=2), encoding="utf-8"
            )

            self.db.mark_asset_processed(asset_id, True)
            self.db.add_extraction_log(
                asset_id=asset_id,
                module_name="image_parser",
                event_type="parse",
                status="parsed",
                detail=f"Image parsed to {metadata_output_path}",
            )
            self.db.append_ledger_event(
                event_type="parse",
                actor="image_parser",
                payload={
                    "status": parse_status,
                    "source_path": str(local_path),
                    "output_text_path": output_text_path,
                    "source_sha256": asset["sha256"],
                    "characters": characters,
                    "image": image_meta,
                },
                crawl_session_id=crawl_session_id,
                asset_id=asset_id,
            )
            return "parsed"
        except Exception as exc:
            self.db.mark_asset_processed(asset_id, False)
            self.db.add_extraction_log(
                asset_id=asset_id,
                module_name="image_parser",
                event_type="parse",
                status="failed",
                detail=str(exc),
            )
            self.db.append_ledger_event(
                event_type="parse",
                actor="image_parser",
                payload={
                    "status": "failed",
                    "source_path": str(local_path),
                    "error": str(exc),
                },
                crawl_session_id=crawl_session_id,
                asset_id=asset_id,
            )
            LOGGER.exception(
                "Image parsing failed",
                extra={"asset_id": asset_id, "local_path": str(local_path)},
            )
            return "failed"

    @staticmethod
    def _extract_ocr_and_metadata(
        local_path: Path,
    ) -> tuple[str, dict[str, int | str | None]]:
        if pytesseract is None or Image is None:
            raise RuntimeError(
                "pytesseract / Pillow are not installed; cannot parse image assets"
            )

        with Image.open(local_path) as image:
            # Normalize palette/alpha images for reliable OCR behavior.
            prepared = image.convert("RGB")
            raw_text = str(
                pytesseract.image_to_string(prepared, config="--psm 6")
            ).strip()
            text = (raw_text + "\n") if raw_text else ""
            image_meta = {
                "width": int(prepared.width),
                "height": int(prepared.height),
                "mode": str(prepared.mode),
                "format": str(image.format) if image.format else None,
            }
        return text, image_meta

    def _output_paths(self, local_path: Path) -> tuple[Path, Path]:
        raw_root = self.project_root / "data" / "raw"
        try:
            relative_path = local_path.resolve().relative_to(raw_root.resolve())
        except ValueError:
            relative_path = Path(local_path.name)

        text_output_path = (self.parsed_root / relative_path).with_suffix(".txt")
        metadata_output_path = text_output_path.with_suffix(".meta.json")
        return text_output_path, metadata_output_path

    @staticmethod
    def _is_current(
        source_sha256: str | None,
        text_output_path: Path,
        metadata_output_path: Path,
    ) -> bool:
        if not source_sha256 or not metadata_output_path.exists():
            return False

        try:
            metadata = json.loads(metadata_output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False

        if metadata.get("source_sha256") != source_sha256:
            return False

        parse_status = str(metadata.get("parse_status", ""))
        if parse_status == "visual_only_no_text":
            return True
        return text_output_path.exists()
