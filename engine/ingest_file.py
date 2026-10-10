"""Ingest a file and print what was extracted.

    python engine\\ingest_file.py path\\to\\file [--password PW] [--no-ocr] [--show-text]

Without --show-text the output is a summary only (methods, page quality,
record counts, warnings, failures), safe to share for debugging.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ingest import ingest_path  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--password")
    ap.add_argument("--no-ocr", action="store_true")
    ap.add_argument("--show-text", action="store_true")
    a = ap.parse_args()
    docs, fails = ingest_path(a.path, password=a.password, allow_ocr=not a.no_ocr)
    out = {"documents": [], "failures": [f.__dict__ for f in fails]}
    for d in docs:
        item = {
            "doc_id": d.doc_id, "file": d.file_name, "media_type": d.media_type, "parent": d.parent_id,
            "pages": [{"n": p.number, "method": p.method, "lock": p.initial_lock, "chars": p.quality.chars,
                       "ocr_confidence": p.quality.ocr_mean_confidence, "flags": p.quality.flags} for p in d.pages],
            "structured_records": len(d.structured), "metadata_keys": sorted(d.metadata), "warnings": d.warnings,
        }
        if a.show_text:
            item["text"] = d.canonical_text
        out["documents"].append(item)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 1 if fails and not docs else 0


if __name__ == "__main__":
    sys.exit(main())
