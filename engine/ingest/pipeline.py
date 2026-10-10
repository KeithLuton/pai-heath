"""Orchestrate document ingest: local path or HTTPS URL → page images + report.

Usage from the engine:

    from ingest.pipeline import ingest

    bundle, report = ingest("/path/to/lab.pdf", out_root="/data/ingest")
    bundle, report = ingest("https://portal.example/report.pdf", out_root="/data/ingest",
                            download=True)

Shareable report (report.as_dict()) contains hashes, page dimensions, and notes
— never page text or clinical values.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

from .download import DownloadError, download
from .pdf_pages import DEFAULT_DPI, IngestError, ingest_file
from .schema import DocumentBundle, IngestReport


def _is_url(s: str) -> bool:
    try:
        u = urlparse(s)
        return u.scheme in ("http", "https") and bool(u.netloc)
    except Exception:
        return False


def ingest(
    source: str,
    out_root: str | Path,
    *,
    download_allowed: bool = False,
    dpi: int = DEFAULT_DPI,
    first_page: Optional[int] = None,
    last_page: Optional[int] = None,
    max_download_bytes: int = 50 * 1024 * 1024,
) -> Tuple[DocumentBundle, IngestReport]:
    """Ingest a local file or (if download_allowed) an HTTPS URL.

    Layout under out_root:
        <sha256>/
            original.<ext>          # only when downloaded; local paths are not copied
            pages/
                page_0001.png
                page_0002.png
                ...
            report.json             # shareable IngestReport
    """
    t0 = time.time()
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    report = IngestReport()
    local_path: Path
    status = "LOCAL_RASTER"

    if _is_url(source):
        if not download_allowed:
            raise IngestError(
                "source is a URL but download is not enabled; "
                "pass download_allowed=True after the user confirms the address"
            )
        if urlparse(source).scheme != "https":
            raise DownloadError("only https:// URLs are accepted")
        staging = out_root / "_downloads"
        result = download(source, staging, max_bytes=max_download_bytes)
        local_path = result.path
        status = "DOWNLOADED_THEN_RASTER"
        report.notes.append(
            f"Downloaded from https://{urlparse(source).netloc}/… "
            f"({result.bytes_written} bytes, {result.media_type})"
        )
        report.source = f"https://{urlparse(source).netloc}/"  # host only in shareable report
    else:
        local_path = Path(source)
        report.source = local_path.name  # basename only in shareable report

    # Pre-hash so the work directory is content-addressed even before full ingest
    # (ingest_file re-hashes; identical for local files).
    work = out_root / "pending"
    pages_dir = work / "pages"
    try:
        bundle = ingest_file(
            local_path,
            pages_dir,
            dpi=dpi,
            first_page=first_page,
            last_page=last_page,
        )
    except (IngestError, DownloadError):
        raise
    except Exception as exc:
        report.rejected.append(str(exc))
        report.seconds = round(time.time() - t0, 2)
        raise IngestError(str(exc)) from exc

    # Move pending → <sha256>
    final = out_root / bundle.document_id
    if final.exists():
        # Same content already ingested; replace pages if re-run at different DPI
        import shutil
        shutil.rmtree(final, ignore_errors=True)
    work.rename(final)
    # Fix page paths after rename
    new_pages = []
    for p in bundle.pages:
        new_path = str((final / "pages" / Path(p.path).name).resolve())
        new_pages.append(type(p)(
            page_index=p.page_index,
            path=new_path,
            width_px=p.width_px,
            height_px=p.height_px,
            dpi=p.dpi,
            sha256=p.sha256,
            source=p.source,
        ))
    bundle = DocumentBundle(
        document_id=bundle.document_id,
        original_path=bundle.original_path,
        original_sha256=bundle.original_sha256,
        media_type=bundle.media_type,
        page_count=bundle.page_count,
        pages=new_pages,
        embedded_text_chars=bundle.embedded_text_chars,
        notes=list(bundle.notes),
    )

    report.importer_status = status
    report.original_sha256 = bundle.original_sha256
    report.media_type = bundle.media_type
    report.page_count = bundle.page_count
    report.image_dir = str((final / "pages").resolve())
    report.pages = [
        {
            "page_index": p.page_index,
            "width_px": p.width_px,
            "height_px": p.height_px,
            "dpi": p.dpi,
            "sha256": p.sha256,
            "source": p.source,
        }
        for p in bundle.pages
    ]
    report.embedded_text_chars = bundle.embedded_text_chars
    report.notes.extend(bundle.notes)
    report.seconds = round(time.time() - t0, 2)

    (final / "report.json").write_text(
        json.dumps(report.as_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return bundle, report
