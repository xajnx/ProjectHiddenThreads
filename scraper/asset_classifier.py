from __future__ import annotations

from pathlib import PurePosixPath
from typing import Optional
from urllib.parse import urlparse

ASSET_TYPE_DOCUMENT = "document"
ASSET_TYPE_VIDEO = "video"
ASSET_TYPE_AUDIO = "audio"
ASSET_TYPE_IMAGE = "image"
ASSET_TYPE_ARCHIVE = "archive"
ASSET_TYPE_WEB = "web"
ASSET_TYPE_OTHER = "other"

EXTENSION_MAP = {
    ".pdf": ASSET_TYPE_DOCUMENT,
    ".docx": ASSET_TYPE_DOCUMENT,
    ".txt": ASSET_TYPE_DOCUMENT,
    ".csv": ASSET_TYPE_DOCUMENT,
    ".xlsx": ASSET_TYPE_DOCUMENT,
    ".pptx": ASSET_TYPE_DOCUMENT,
    ".zip": ASSET_TYPE_ARCHIVE,
    ".mp4": ASSET_TYPE_VIDEO,
    ".mov": ASSET_TYPE_VIDEO,
    ".avi": ASSET_TYPE_VIDEO,
    ".mkv": ASSET_TYPE_VIDEO,
    ".webm": ASSET_TYPE_VIDEO,
    ".m3u8": ASSET_TYPE_VIDEO,
    ".mp3": ASSET_TYPE_AUDIO,
    ".wav": ASSET_TYPE_AUDIO,
    ".jpg": ASSET_TYPE_IMAGE,
    ".jpeg": ASSET_TYPE_IMAGE,
    ".png": ASSET_TYPE_IMAGE,
    ".tif": ASSET_TYPE_IMAGE,
    ".tiff": ASSET_TYPE_IMAGE,
    ".gif": ASSET_TYPE_IMAGE,
    ".svg": ASSET_TYPE_IMAGE,
    ".html": ASSET_TYPE_WEB,
    ".htm": ASSET_TYPE_WEB,
    ".json": ASSET_TYPE_WEB,
    ".xml": ASSET_TYPE_WEB,
}

MIME_PREFIX_MAP = {
    "application/pdf": ASSET_TYPE_DOCUMENT,
    "application/vnd.openxmlformats-officedocument": ASSET_TYPE_DOCUMENT,
    "text/plain": ASSET_TYPE_DOCUMENT,
    "text/csv": ASSET_TYPE_DOCUMENT,
    "application/zip": ASSET_TYPE_ARCHIVE,
    "video/": ASSET_TYPE_VIDEO,
    "audio/": ASSET_TYPE_AUDIO,
    "image/": ASSET_TYPE_IMAGE,
    "text/html": ASSET_TYPE_WEB,
    "application/json": ASSET_TYPE_WEB,
    "application/xml": ASSET_TYPE_WEB,
    "text/xml": ASSET_TYPE_WEB,
}


def classify_asset(url: str, mime_type: Optional[str] = None) -> str:
    """Classify a URL using extension first, then MIME-type fallbacks."""

    parsed = urlparse(url)
    suffix = PurePosixPath(parsed.path.lower()).suffix

    if suffix in EXTENSION_MAP:
        return EXTENSION_MAP[suffix]

    if mime_type:
        normalized = mime_type.lower().split(";", 1)[0].strip()
        for prefix, asset_type in MIME_PREFIX_MAP.items():
            if normalized == prefix or normalized.startswith(prefix):
                return asset_type

    if parsed.path.endswith("/") or not suffix:
        return ASSET_TYPE_WEB

    return ASSET_TYPE_OTHER


def is_downloadable_asset(asset_type: str) -> bool:
    return asset_type in {
        ASSET_TYPE_DOCUMENT,
        ASSET_TYPE_VIDEO,
        ASSET_TYPE_AUDIO,
        ASSET_TYPE_IMAGE,
        ASSET_TYPE_ARCHIVE,
    }
