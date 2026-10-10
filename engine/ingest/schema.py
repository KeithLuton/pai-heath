"""Ingest contract for medical documents — parallel to devices/schema.py.

A document enters as a file (PDF, image, or a downloaded PDF). The ingest layer
never extracts clinical meaning: it produces page images, hashes, and a
shareable report (counts, page sizes, media types — no text, no PHI).

Downstream OCR / float extraction reads the page images (and optional
embedded text) under the engine's own rules.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class PageImage:
    """One rendered page on disk."""
    page_index: int              # 0-based
    path: str                    # absolute path to PNG (or JPEG)
    width_px: int
    height_px: int
    dpi: int
    sha256: str                  # of the image bytes
    source: str                  # "pdf_raster" | "image_passthrough" | "pdf_embedded_preview"


@dataclass(frozen=True)
class DocumentBundle:
    """Result of ingesting one input file."""
    document_id: str             # stable id: sha256 of original bytes (hex, full)
    original_path: str           # as supplied (or download destination)
    original_sha256: str
    media_type: str              # application/pdf | image/png | image/jpeg | ...
    page_count: int
    pages: List[PageImage]
    embedded_text_chars: int = 0  # total chars from PDF text layer if any; 0 for scans
    notes: List[str] = field(default_factory=list)


@dataclass
class IngestReport:
    """Shareable summary — no page text, no clinical values."""
    importer: str = "document_ingest"
    importer_status: str = "LOCAL_RASTER"   # or "DOWNLOADED_THEN_RASTER"
    source: str = ""                        # path or URL (URL host only in report if desired)
    original_sha256: str = ""
    media_type: str = ""
    page_count: int = 0
    image_dir: str = ""
    pages: list = field(default_factory=list)   # [{page_index, width_px, height_px, dpi, sha256}]
    embedded_text_chars: int = 0
    rejected: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    seconds: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)
