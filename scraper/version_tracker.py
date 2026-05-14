from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path


class VersionTracker:
    """Moves superseded files into version storage before replacement."""

    def __init__(self, versions_root: Path) -> None:
        self.versions_root = versions_root
        self.versions_root.mkdir(parents=True, exist_ok=True)

    def archive_existing_file(self, current_path: Path, asset_id: int) -> Path:
        if not current_path.exists():
            raise FileNotFoundError(f"Cannot archive missing file: {current_path}")

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        archived_name = f"asset_{asset_id}_{timestamp}{current_path.suffix.lower()}"
        destination = self.versions_root / archived_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(current_path), destination)
        return destination
