# ProjectHiddenThreads

ProjectHiddenThreads is a modular, evidence-driven archival pipeline for long-term collection and analysis of public UAP/UFO records, media, and supporting metadata.

## Current Baseline (v0.1)

This implementation now follows a production-oriented three-phase workflow:

- Discovery phase: pagination-aware crawling, static script URL extraction, deduplicated link indexing.
- Verification phase: NEW / UNCHANGED / MODIFIED / BLOCKED / UNKNOWN classification using HEAD metadata.
- Acquisition phase: download only NEW and MODIFIED assets when access is permitted.
- Policy-compliant blocked handling: 401/403 is treated as terminal blocked state with metadata retention.
- Incremental tracking with first seen / last seen timestamps and historical version records.
- JSON index output (`data/metadata/assets_index.json`) and markdown/html reports (`data/reports`).

## Project Layout

- `main.py`: Pipeline entrypoint.
- `database/schema.sql`: SQLite schema for assets, versions, sessions, ledger, and analysis placeholders.
- `database/db.py`: Database adapter and repository operations.
- `scraper/crawler.py`: Recursive discovery engine.
- `scraper/discovery_parser.py`: HTML + script URL extraction and normalization.
- `scraper/verifier.py`: Verification phase state classifier.
- `scraper/downloader.py`: Concurrent downloader with change/version tracking.
- `scraper/asset_classifier.py`: URL/MIME-based asset typing.
- `scraper/checksum.py`: SHA256 hashing utilities.
- `scraper/version_tracker.py`: Historical file archiving.
- `scraper/evidence_grader.py`: Tier assignment logic and rationale generation.
- `config/config.yaml.example`: Runtime configuration template.
- `config/logging.yaml`: Structured logging configuration.
- `analysis/reporting.py`: Markdown/HTML report + JSON index generation.

## Setup

1. Create and activate a Python 3.12+ virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

1. Create runtime config:

```bash
cp config/config.yaml.example config/config.yaml
```

1. Update `config/config.yaml` with your target domains and constraints.

## Script Initialization

The entry script initializes in this order:

1. Parse CLI arguments (`--config`, `--skip-crawl`, `--skip-verify`, `--skip-parse`, `--seed-url-file`).
1. Optionally import a pre-downloaded local release into canonical raw storage (`--import-local-release-dir`).
1. Initialize logging from `config/logging.yaml`.
1. Load and resolve YAML config paths.
1. Ensure required data/log directories exist.
1. Initialize SQLite schema and compatibility columns.
1. Start a crawl session record.
1. Run discovery crawler (unless `--skip-crawl`).
1. Run verification classifier (unless `--skip-verify`).
1. Run acquisition downloader.
1. Extract text from supported local document assets unless `--skip-parse` is set.
1. Generate report outputs and JSON asset index.
1. Close the crawl session with final status and metrics.

## Run

```bash
python main.py --config config/config.yaml
```

Optional:

```bash
python main.py --config config/config.yaml --skip-crawl
```

Skip verification phase:

```bash
python main.py --config config/config.yaml --skip-verify
```

Explicit venv invocation:

```bash
./.venv/bin/python main.py --config config/config.yaml
```

Bulk import from authorized URL list (TXT/CSV) and download without crawling:

```bash
./.venv/bin/python main.py \
  --config config/config.yaml \
  --skip-crawl \
  --seed-url-file data/metadata/authorized_urls.txt
```

`--seed-url-file` supports:

- TXT: one URL per line (`#` comments allowed).
- CSV: first column or `url` column header.

Import a local release payload that has already been downloaded, then regenerate reports/indexes without re-downloading:

```bash
./.venv/bin/python main.py \
  --config config/config.yaml \
  --skip-crawl \
  --skip-verify \
  --import-local-release-dir direct_download/release_1
```

This import mode:

- Copies files into `data/raw/*/release_1/...` by asset type.
- Registers each file in SQLite and `data/metadata/assets_index.json`.
- Preserves source grouping from the release folder (`DeptOfWar`, `FBI`, `NASA`, etc.).
- Archives superseded imported files into `data/versions` on re-import.

Document parsing runs automatically after acquisition/import for supported `.txt` and `.pdf` assets. Parsed text is written under `data/parsed_text` with adjacent `.meta.json` sidecars keyed to the source asset checksum, so re-runs skip unchanged files.

## Local Dashboard

Launch the local HTML dashboard server:

```bash
./.venv/bin/python visualizations/dashboard/server.py --host 127.0.0.1 --port 18117
```

Then open:

```text
http://127.0.0.1:18117
```

Dashboard features:

- Reads indexed assets from SQLite (`database/archive.db`).
- Reads local records from `data/metadata`, `data/reports`, and `data/parsed_text`.
- Searchable, filterable index by kind, type, status, source domain, evidence tier, and discovered date range.
- Inline preview support for text, images, audio, video, and PDFs.

## Data Integrity and Chain-of-Custody

- Raw files are stored under `data/raw/*` and are never modified in place.
- Superseded files are moved to `data/versions` before replacement.
- `assets` and `asset_versions` tables retain state and revision history.
- `assets.verification_state` and `assets.download_status` track pre-acquisition decisions.
- `assets.first_seen_timestamp` and `assets.last_seen_timestamp` support incremental reconciliation.
- `assets.evidence_tier` and `assets.evidence_rationale` store grading outcomes.
- `evidence_grades` stores grading history over time.
- `evidence_ledger` records traceable events with payload hashing.

## Next Build Targets

- Parser pipeline (PyMuPDF + OCR fallback).
- Extraction logs for parsing/OCR workflows.
- Entity and embedding generation jobs.
- Timeline and relationship visualizations.
