"""Ingest a medical PDF or image into page pictures; print the shareable report.

The report holds page counts, dimensions, hashes, and notes — no document text
and no clinical values — so it can be shared to verify the pipeline.

    python engine/import_document.py lab.pdf --out /tmp/ingest
    python engine/import_document.py lab.pdf --out /tmp/ingest --dpi 300
    python engine/import_document.py photo.png --out /tmp/ingest
    python engine/import_document.py https://example.com/report.pdf --out /tmp/ingest --download

Page PNGs land in <out>/<sha256>/pages/page_0001.png …
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest.pipeline import ingest  # noqa: E402
from ingest.pdf_pages import IngestError  # noqa: E402
from ingest.download import DownloadError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", help="Local path to PDF/image, or https URL with --download")
    p.add_argument("--out", required=True, help="Output root directory for page images")
    p.add_argument("--dpi", type=int, default=200, help="PDF raster DPI (default 200)")
    p.add_argument("--first-page", type=int, default=None)
    p.add_argument("--last-page", type=int, default=None)
    p.add_argument(
        "--download",
        action="store_true",
        help="Allow HTTPS download when source is a URL (off by default)",
    )
    args = p.parse_args(argv)

    try:
        bundle, report = ingest(
            args.source,
            args.out,
            download_allowed=args.download,
            dpi=args.dpi,
            first_page=args.first_page,
            last_page=args.last_page,
        )
    except (IngestError, DownloadError) as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        return 1

    out = report.as_dict()
    # Attach non-PHI paths for local operators (not for sharing)
    out["document_id"] = bundle.document_id
    out["page_paths"] = [pg.path for pg in bundle.pages]
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
