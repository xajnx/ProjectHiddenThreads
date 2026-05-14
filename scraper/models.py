from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(slots=True)
class DiscoveredAsset:
    """Canonical representation of a discovered URL and its crawl context."""

    url: str
    parent_page: Optional[str]
    source_domain: str
    asset_type: str
    mime_type: Optional[str] = None
    evidence_tier: int = 3
    evidence_rationale: str = "Default tier assigned."
    discovered_at: datetime = field(default_factory=datetime.utcnow)
