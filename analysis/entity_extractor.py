from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from database.db import Database

LOGGER = logging.getLogger(__name__)

EXTRACTOR_VERSION = "heuristic_v1"

MIN_CONFIDENCE = 0.4
MIN_ENTITY_TEXT_LEN = 2

URL_RE = re.compile(r"https?://[^\s)\]>]+", re.IGNORECASE)
DATE_RE = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+\d{4}|(?:19|20)\d{2})\b",
    re.IGNORECASE,
)
FILE_ID_RE = re.compile(
    r"\b(?:[A-Z]{1,4}-\d{1,6}|\d{1,4}[ _-][A-Z]{2,6}[ _-]\d{1,8}|\d{2}-HQ-\d{3,8})\b"
)

AGENCY_PATTERNS: dict[str, tuple[str, str]] = {
    "Federal Bureau of Investigation": ("AGENCY", "fbi"),
    "FBI": ("AGENCY", "fbi"),
    "CIA": ("AGENCY", "cia"),
    "NSA": ("AGENCY", "nsa"),
    "NRO": ("AGENCY", "nro"),
    "DoD": ("AGENCY", "dod"),
    "Department of Defense": ("AGENCY", "dod"),
    "Department of State": ("AGENCY", "state.gov"),
    "Department of War": ("AGENCY", "war.gov"),
    "NASA": ("AGENCY", "nasa"),
    "U.S. Air Force": ("AGENCY", "usaf"),
    "USAF": ("AGENCY", "usaf"),
}

EVENT_KEYWORDS: dict[str, str] = {
    "saw": "sighting",
    "observed": "sighting",
    "reported": "report",
    "interviewed": "interview",
    "stated": "statement",
    "confirmed": "confirmation",
    "detected": "detection",
    "landed": "landing",
}


@dataclass(slots=True)
class EntityExtractionResult:
    analyzed: int = 0
    skipped: int = 0
    failed: int = 0
    entities_written: int = 0
    events_written: int = 0


@dataclass(slots=True)
class EntityCandidate:
    entity_type: str
    entity_text: str
    normalized_text: str
    confidence: float
    context: str


@dataclass(slots=True)
class EventCandidate:
    event_type: str
    event_text: str
    event_date: str | None
    confidence: float


class EntityExtractor:
    def __init__(self, db: Database, project_root: Path) -> None:
        self.db = db
        self.project_root = project_root
        self.parsed_root = project_root / "data" / "parsed_text"

    def run(self, crawl_session_id: int) -> EntityExtractionResult:
        result = EntityExtractionResult()
        for sidecar_path in sorted(self.parsed_root.rglob("*.meta.json")):
            status = self._process_sidecar(sidecar_path, crawl_session_id, result)
            if status == "analyzed":
                result.analyzed += 1
            elif status == "skipped":
                result.skipped += 1
            else:
                result.failed += 1
        return result

    def _process_sidecar(
        self,
        sidecar_path: Path,
        crawl_session_id: int,
        result: EntityExtractionResult,
    ) -> str:
        try:
            payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
            asset_id = int(payload.get("asset_id"))
            source_sha256 = str(payload.get("source_sha256") or "").strip()
            output_text_path = payload.get("output_text_path")

            if not source_sha256:
                self.db.add_extraction_log(
                    asset_id=asset_id,
                    module_name="entity_extractor",
                    event_type="analyze",
                    status="skipped",
                    detail="Sidecar missing source_sha256",
                )
                return "skipped"

            text = ""
            if output_text_path:
                text_path = Path(str(output_text_path))
                if not text_path.is_absolute():
                    text_path = self.project_root / text_path
                if text_path.exists():
                    text = text_path.read_text(encoding="utf-8", errors="replace")

            if not text.strip():
                self.db.replace_extractions_for_asset(
                    asset_id=asset_id,
                    source_sha256=source_sha256,
                    extractor_version=EXTRACTOR_VERSION,
                )
                self.db.add_extraction_log(
                    asset_id=asset_id,
                    module_name="entity_extractor",
                    event_type="analyze",
                    status="skipped",
                    detail="No parsed text available for entity extraction",
                )
                return "skipped"

            candidates = self._extract_entities(text)
            events = self._extract_events(text)

            self.db.replace_extractions_for_asset(
                asset_id=asset_id,
                source_sha256=source_sha256,
                extractor_version=EXTRACTOR_VERSION,
            )

            written_entities = self._persist_entities(
                asset_id, source_sha256, candidates
            )
            written_events = self._persist_events(asset_id, source_sha256, events)
            result.entities_written += written_entities
            result.events_written += written_events

            self.db.add_extraction_log(
                asset_id=asset_id,
                module_name="entity_extractor",
                event_type="analyze",
                status="parsed",
                detail=(
                    f"Extracted {written_entities} entities and {written_events} events "
                    f"from {sidecar_path.name}"
                ),
            )
            self.db.append_ledger_event(
                event_type="analyze",
                actor="entity_extractor",
                payload={
                    "status": "parsed",
                    "asset_id": asset_id,
                    "source_sha256": source_sha256,
                    "extractor_version": EXTRACTOR_VERSION,
                    "entities": written_entities,
                    "events": written_events,
                },
                crawl_session_id=crawl_session_id,
                asset_id=asset_id,
            )
            return "analyzed"
        except Exception as exc:
            detail = f"Entity extraction failed for {sidecar_path.name}: {exc}"
            asset_id_val = self._safe_sidecar_asset_id(sidecar_path)
            if asset_id_val is not None:
                self.db.add_extraction_log(
                    asset_id=asset_id_val,
                    module_name="entity_extractor",
                    event_type="analyze",
                    status="failed",
                    detail=detail,
                )
            LOGGER.exception(detail)
            return "failed"

    @staticmethod
    def _safe_sidecar_asset_id(sidecar_path: Path) -> int | None:
        try:
            payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
            return int(payload.get("asset_id"))
        except Exception:
            return None

    @staticmethod
    def _context_slice(text: str, start: int, end: int, width: int = 72) -> str:
        left = max(0, start - width)
        right = min(len(text), end + width)
        return " ".join(text[left:right].split())

    def _extract_entities(self, text: str) -> list[EntityCandidate]:
        candidates: list[EntityCandidate] = []

        for match in URL_RE.finditer(text):
            entity = match.group(0)
            candidates.append(
                EntityCandidate(
                    entity_type="URL",
                    entity_text=entity,
                    normalized_text=entity.lower(),
                    confidence=0.99,
                    context=self._context_slice(text, match.start(), match.end()),
                )
            )

        for match in DATE_RE.finditer(text):
            entity = match.group(0)
            candidates.append(
                EntityCandidate(
                    entity_type="DATE",
                    entity_text=entity,
                    normalized_text=entity.lower(),
                    confidence=0.88,
                    context=self._context_slice(text, match.start(), match.end()),
                )
            )

        for match in FILE_ID_RE.finditer(text):
            entity = match.group(0)
            candidates.append(
                EntityCandidate(
                    entity_type="FILE_ID",
                    entity_text=entity,
                    normalized_text=entity.lower(),
                    confidence=0.86,
                    context=self._context_slice(text, match.start(), match.end()),
                )
            )

        lowered = text.lower()
        for phrase, (entity_type, normalized) in AGENCY_PATTERNS.items():
            start = 0
            needle = phrase.lower()
            while True:
                idx = lowered.find(needle, start)
                if idx == -1:
                    break
                end = idx + len(needle)
                candidates.append(
                    EntityCandidate(
                        entity_type=entity_type,
                        entity_text=phrase,
                        normalized_text=normalized,
                        confidence=0.93,
                        context=self._context_slice(text, idx, end),
                    )
                )
                start = end

        return candidates

    def _extract_events(self, text: str) -> list[EventCandidate]:
        events: list[EventCandidate] = []
        for raw_line in text.splitlines():
            line = " ".join(raw_line.split())
            if len(line) < 25:
                continue

            lowered = line.lower()
            matched_type: str | None = None
            for key, event_type in EVENT_KEYWORDS.items():
                if key in lowered:
                    matched_type = event_type
                    break

            if not matched_type:
                continue

            date_match = DATE_RE.search(line)
            events.append(
                EventCandidate(
                    event_type=matched_type,
                    event_text=line[:360],
                    event_date=date_match.group(0) if date_match else None,
                    confidence=0.72,
                )
            )

            if len(events) >= 25:
                break

        return events

    def _persist_entities(
        self,
        asset_id: int,
        source_sha256: str,
        candidates: list[EntityCandidate],
    ) -> int:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for candidate in candidates:
            key = (candidate.entity_type, candidate.normalized_text)
            bucket = grouped.setdefault(
                key,
                {
                    "entity_text": candidate.entity_text,
                    "confidence": candidate.confidence,
                    "occurrences": 0,
                    "contexts": [],
                },
            )
            occurrences = bucket.get("occurrences")
            if not isinstance(occurrences, int):
                occurrences = 0
            bucket["occurrences"] = occurrences + 1
            contexts = bucket["contexts"]
            if isinstance(contexts, list) and len(contexts) < 3:
                contexts.append(candidate.context)

        written = 0
        for (entity_type, normalized_text), data in grouped.items():
            entity_text = str(data.get("entity_text") or normalized_text)
            confidence_raw = data.get("confidence")
            occurrences_raw = data.get("occurrences")
            contexts_raw = data.get("contexts")
            confidence = (
                confidence_raw if isinstance(confidence_raw, (int, float)) else 0.5
            )
            occurrences = occurrences_raw if isinstance(occurrences_raw, int) else 1
            contexts = contexts_raw if isinstance(contexts_raw, list) else []

            # Confidence and text length gating — suppress noisy OCR artifacts
            if float(confidence) < MIN_CONFIDENCE:
                continue
            if len(normalized_text.strip()) < MIN_ENTITY_TEXT_LEN:
                continue

            self.db.add_extracted_entity(
                asset_id=asset_id,
                source_sha256=source_sha256,
                extractor_version=EXTRACTOR_VERSION,
                entity_type=entity_type,
                entity_text=entity_text,
                normalized_text=normalized_text,
                confidence=float(confidence),
                occurrences=occurrences,
                contexts_json=json.dumps(contexts),
            )
            written += 1
        return written

    def _persist_events(
        self,
        asset_id: int,
        source_sha256: str,
        events: list[EventCandidate],
    ) -> int:
        written = 0
        for event in events:
            self.db.add_extracted_event(
                asset_id=asset_id,
                source_sha256=source_sha256,
                extractor_version=EXTRACTOR_VERSION,
                event_type=event.event_type,
                event_text=event.event_text,
                event_date=event.event_date,
                confidence=event.confidence,
            )
            written += 1
        return written
