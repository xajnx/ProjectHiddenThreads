PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS source_domains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS crawl_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    seed_domains TEXT NOT NULL,
    status TEXT NOT NULL,
    pages_crawled INTEGER NOT NULL DEFAULT 0,
    assets_discovered INTEGER NOT NULL DEFAULT 0,
    assets_downloaded INTEGER NOT NULL DEFAULT 0,
    assets_blocked INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    source_domain TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    mime_type TEXT,
    first_seen_timestamp TEXT,
    last_seen_timestamp TEXT,
    download_status TEXT NOT NULL DEFAULT 'new',
    verification_state TEXT NOT NULL DEFAULT 'unknown',
    file_hash TEXT,
    content_length INTEGER,
    sha256 TEXT,
    size_bytes INTEGER,
    discovered_at TEXT NOT NULL,
    downloaded_at TEXT,
    last_modified TEXT,
    etag TEXT,
    local_path TEXT,
    evidence_tier INTEGER NOT NULL DEFAULT 3,
    evidence_rationale TEXT,
    processed INTEGER NOT NULL DEFAULT 0,
    parent_page_url TEXT,
    parent_page TEXT,
    status TEXT NOT NULL DEFAULT 'discovered',
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_assets_domain ON assets(source_domain);
CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(status);
CREATE INDEX IF NOT EXISTS idx_assets_sha256 ON assets(sha256);
CREATE INDEX IF NOT EXISTS idx_assets_evidence_tier ON assets(evidence_tier);

CREATE TABLE IF NOT EXISTS evidence_grades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    tier INTEGER NOT NULL,
    rationale TEXT NOT NULL,
    method TEXT NOT NULL,
    graded_at TEXT NOT NULL,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS asset_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    previous_sha256 TEXT,
    previous_path TEXT,
    archived_path TEXT NOT NULL,
    archived_at TEXT NOT NULL,
    change_reason TEXT NOT NULL,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER,
    entity_type TEXT NOT NULL,
    entity_value TEXT NOT NULL,
    confidence REAL,
    extracted_at TEXT NOT NULL,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER,
    keyword TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 1,
    extracted_at TEXT NOT NULL,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER,
    model_name TEXT NOT NULL,
    vector_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS extraction_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER,
    module_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS extracted_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    source_sha256 TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    occurrences INTEGER NOT NULL DEFAULT 1,
    contexts_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE,
    UNIQUE (asset_id, source_sha256, extractor_version, entity_type, normalized_text)
);

CREATE INDEX IF NOT EXISTS idx_extracted_entities_asset ON extracted_entities(asset_id);
CREATE INDEX IF NOT EXISTS idx_extracted_entities_type ON extracted_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_extracted_entities_norm ON extracted_entities(normalized_text);

CREATE TABLE IF NOT EXISTS extracted_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    source_sha256 TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_text TEXT NOT NULL,
    event_date TEXT,
    confidence REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_extracted_events_asset ON extracted_events(asset_id);
CREATE INDEX IF NOT EXISTS idx_extracted_events_type ON extracted_events(event_type);

CREATE TABLE IF NOT EXISTS evidence_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crawl_session_id INTEGER,
    asset_id INTEGER,
    event_type TEXT NOT NULL,
    event_time TEXT NOT NULL,
    actor TEXT NOT NULL,
    event_payload TEXT,
    payload_sha256 TEXT,
    FOREIGN KEY (crawl_session_id) REFERENCES crawl_sessions(id) ON DELETE SET NULL,
    FOREIGN KEY (asset_id) REFERENCES assets(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_ledger_event_time ON evidence_ledger(event_time);
CREATE INDEX IF NOT EXISTS idx_ledger_event_type ON evidence_ledger(event_type);
