"""Document ingestion.

Two complementary paths:
  * text path   — ingest_bytes / ingest_path: any supported file -> canonical text,
                  per-page method and quality, structured records (ingest.py)
  * image path  — ingest / ingest_file / download: PDF or image -> page pictures for
                  the page checker and OCR, plus a shareable report (pipeline.py)
"""
from .ingest import SUPPORTED, ingest_bytes, ingest_path  # noqa: F401
from .schema import DocumentBundle, IngestReport, PageImage  # noqa: F401
from .pdf_pages import DEFAULT_DPI, IngestError, ingest_file  # noqa: F401
from .download import DownloadError, download  # noqa: F401
from .pipeline import ingest  # noqa: F401
