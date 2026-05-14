from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from database.db import Database

try:
    import fitz
except ImportError:  # pragma: no cover - exercised only when dependency is missing.
    fitz = None

try:
    import pytesseract
    from PIL import Image
except ImportError:  # pragma: no cover - exercised only when dependency is missing.
    pytesseract = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment]

LOGGER = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = {".pdf", ".txt"}


class OcrRequiredError(RuntimeError):
    pass


class VisualOnlyAssetError(RuntimeError):
    pass


@dataclass(slots=True)
class DocumentParseResult:
    parsed: int = 0
    skipped: int = 0
    failed: int = 0
    unsupported: int = 0


class DocumentParser:
    def __init__(self, db: Database, project_root: Path) -> None:
        self.db = db
        self.project_root = project_root
        self.parsed_root = project_root / "data" / "parsed_text"

    def run(self, crawl_session_id: int) -> DocumentParseResult:
        result = DocumentParseResult()
        for asset in self.db.list_assets_for_parsing():
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
                module_name="document_parser",
                event_type="parse",
                status="unsupported",
                detail=f"Unsupported document suffix: {suffix or '<none>'}",
            )
            return "unsupported"

        if not local_path.exists():
            detail = f"Local asset missing: {local_path}"
            self.db.add_extraction_log(
                asset_id=asset_id,
                module_name="document_parser",
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
                module_name="document_parser",
                event_type="parse",
                status="skipped",
                detail=f"Parsed text is current: {text_output_path}",
            )
            return "skipped"

        try:
            extracted_text = self._extract_text(local_path)
            text_output_path.parent.mkdir(parents=True, exist_ok=True)
            text_output_path.write_text(extracted_text, encoding="utf-8")

            metadata = {
                "asset_id": asset_id,
                "source_url": asset["url"],
                "source_path": str(local_path),
                "source_sha256": asset["sha256"],
                "source_mime_type": asset["mime_type"],
                "parser": self._parser_name_for_suffix(suffix),
                "parsed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "characters": len(extracted_text),
                "output_text_path": str(text_output_path),
                "parse_status": "parsed",
            }
            metadata_output_path.write_text(
                json.dumps(metadata, indent=2),
                encoding="utf-8",
            )

            self.db.mark_asset_processed(asset_id, True)
            self.db.add_extraction_log(
                asset_id=asset_id,
                module_name="document_parser",
                event_type="parse",
                status="parsed",
                detail=f"Parsed to {text_output_path}",
            )
            self.db.append_ledger_event(
                event_type="parse",
                actor="document_parser",
                payload={
                    "status": "parsed",
                    "source_path": str(local_path),
                    "output_text_path": str(text_output_path),
                    "source_sha256": asset["sha256"],
                    "characters": len(extracted_text),
                },
                crawl_session_id=crawl_session_id,
                asset_id=asset_id,
            )
            return "parsed"
        except OcrRequiredError:
            # Native text extraction empty — attempt OCR on rendered page images.
            try:
                extracted_text = self._ocr_pdf(local_path)
                text_output_path.parent.mkdir(parents=True, exist_ok=True)
                text_output_path.write_text(extracted_text, encoding="utf-8")

                metadata = {
                    "asset_id": asset_id,
                    "source_url": asset["url"],
                    "source_path": str(local_path),
                    "source_sha256": asset["sha256"],
                    "source_mime_type": asset["mime_type"],
                    "parser": "pymupdf+tesseract",
                    "parsed_at": datetime.now(timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                    "characters": len(extracted_text),
                    "output_text_path": str(text_output_path),
                    "parse_status": "parsed_ocr",
                }
                metadata_output_path.write_text(
                    json.dumps(metadata, indent=2),
                    encoding="utf-8",
                )

                self.db.mark_asset_processed(asset_id, True)
                self.db.add_extraction_log(
                    asset_id=asset_id,
                    module_name="document_parser",
                    event_type="parse",
                    status="parsed",
                    detail=f"OCR parsed to {text_output_path}",
                )
                self.db.append_ledger_event(
                    event_type="parse",
                    actor="document_parser",
                    payload={
                        "status": "parsed_ocr",
                        "source_path": str(local_path),
                        "output_text_path": str(text_output_path),
                        "source_sha256": asset["sha256"],
                        "characters": len(extracted_text),
                    },
                    crawl_session_id=crawl_session_id,
                    asset_id=asset_id,
                )
                return "parsed"
            except VisualOnlyAssetError as visual_exc:
                detail = str(visual_exc)
                text_output_path.parent.mkdir(parents=True, exist_ok=True)
                metadata_output_path.write_text(
                    json.dumps(
                        {
                            "asset_id": asset_id,
                            "source_url": asset["url"],
                            "source_path": str(local_path),
                            "source_sha256": asset["sha256"],
                            "source_mime_type": asset["mime_type"],
                            "parser": "pymupdf+tesseract",
                            "parsed_at": datetime.now(timezone.utc).strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            ),
                            "characters": 0,
                            "output_text_path": None,
                            "parse_status": "visual_only_no_text",
                            "detail": detail,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                self.db.mark_asset_processed(asset_id, True)
                self.db.add_extraction_log(
                    asset_id=asset_id,
                    module_name="document_parser",
                    event_type="parse",
                    status="parsed",
                    detail=f"Visual-only PDF classified: {detail}",
                )
                self.db.append_ledger_event(
                    event_type="parse",
                    actor="document_parser",
                    payload={
                        "status": "visual_only_no_text",
                        "source_path": str(local_path),
                        "detail": detail,
                    },
                    crawl_session_id=crawl_session_id,
                    asset_id=asset_id,
                )
                return "parsed"
            except Exception as ocr_exc:
                detail = str(ocr_exc)
                text_output_path.parent.mkdir(parents=True, exist_ok=True)
                metadata_output_path.write_text(
                    json.dumps(
                        {
                            "asset_id": asset_id,
                            "source_url": asset["url"],
                            "source_path": str(local_path),
                            "source_sha256": asset["sha256"],
                            "source_mime_type": asset["mime_type"],
                            "parser": "pymupdf+tesseract",
                            "parsed_at": datetime.now(timezone.utc).strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            ),
                            "characters": 0,
                            "output_text_path": None,
                            "parse_status": "ocr_failed",
                            "detail": detail,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                self.db.mark_asset_processed(asset_id, False)
                self.db.add_extraction_log(
                    asset_id=asset_id,
                    module_name="document_parser",
                    event_type="parse",
                    status="failed",
                    detail=f"OCR failed: {detail}",
                )
                self.db.append_ledger_event(
                    event_type="parse",
                    actor="document_parser",
                    payload={
                        "status": "ocr_failed",
                        "source_path": str(local_path),
                        "error": detail,
                    },
                    crawl_session_id=crawl_session_id,
                    asset_id=asset_id,
                )
                LOGGER.error(
                    "OCR fallback failed",
                    extra={
                        "asset_id": asset_id,
                        "local_path": str(local_path),
                        "error": detail,
                    },
                )
                return "failed"
        except Exception as exc:
            self.db.mark_asset_processed(asset_id, False)
            self.db.add_extraction_log(
                asset_id=asset_id,
                module_name="document_parser",
                event_type="parse",
                status="failed",
                detail=str(exc),
            )
            self.db.append_ledger_event(
                event_type="parse",
                actor="document_parser",
                payload={
                    "status": "failed",
                    "source_path": str(local_path),
                    "error": str(exc),
                },
                crawl_session_id=crawl_session_id,
                asset_id=asset_id,
            )
            LOGGER.exception(
                "Document parsing failed",
                extra={"asset_id": asset_id, "local_path": str(local_path)},
            )
            return "failed"

    def _extract_text(self, local_path: Path) -> str:
        suffix = local_path.suffix.lower()
        if suffix == ".txt":
            return self._extract_text_file(local_path)
        if suffix == ".pdf":
            return self._extract_pdf(local_path)
        raise ValueError(f"Unsupported document suffix: {suffix}")

    @staticmethod
    def _extract_text_file(local_path: Path) -> str:
        raw_text = local_path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw_text:
            raise ValueError("Text file is empty after decoding")
        return raw_text + "\n"

    @staticmethod
    def _extract_pdf(local_path: Path) -> str:
        if fitz is None:
            raise RuntimeError("PyMuPDF is not installed; cannot parse PDF assets")

        with fitz.open(local_path) as document:
            page_text = []
            for page in document:
                extract_text = getattr(page, "get_text")
                page_text.append(str(extract_text("text")).strip())

        text_chunks = [chunk for chunk in page_text if chunk]
        if not text_chunks:
            raise OcrRequiredError(
                "PDF contains no extractable text; OCR fallback required"
            )
        return "\n\n".join(text_chunks) + "\n"

    @staticmethod
    def _ocr_pdf(local_path: Path) -> str:
        """Render each PDF page via PyMuPDF and run Tesseract OCR on the image."""
        if fitz is None:
            raise RuntimeError(
                "PyMuPDF is not installed; cannot render PDF pages for OCR"
            )
        if pytesseract is None or Image is None:
            raise RuntimeError("pytesseract / Pillow are not installed; cannot run OCR")

        page_texts: list[str] = []
        image_pages = 0
        with fitz.open(local_path) as document:
            for page in document:
                # Render at 150 DPI — sufficient for OCR, faster than 200 DPI.
                list_images = getattr(page, "get_images")
                if list_images(full=True):
                    image_pages += 1
                render_page = getattr(page, "get_pixmap")
                pixmap = render_page(dpi=150)
                get_samples = getattr(pixmap, "samples")
                img = Image.frombytes("RGB", (pixmap.width, pixmap.height), get_samples)
                text = str(pytesseract.image_to_string(img, config="--psm 3")).strip()
                if text:
                    page_texts.append(text)

        if not page_texts:
            if image_pages > 0:
                raise VisualOnlyAssetError(
                    "No OCR text detected; PDF appears to contain photographic imagery only"
                )
            raise ValueError("OCR produced no text from any page")
        return "\n\n".join(page_texts) + "\n"

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

        # A sidecar without a matching text output (e.g. previous ocr_required)
        # is not considered current — re-attempt on the next run.
        return text_output_path.exists()

    @staticmethod
    def _parser_name_for_suffix(suffix: str) -> str:
        if suffix == ".pdf":
            return "pymupdf"
        if suffix == ".txt":
            return "plain_text"
        return "unknown"
