"""Render one page of a stored document to PNG, in memory.

Used to show the page beside its extracted text in the Ingest tab and to hand
the page to the checker. Nothing is written to disk: page images of health
records only ever exist in memory, the encrypted vault holds the original.
"""
from __future__ import annotations

import io
from typing import Optional

from PIL import Image

MAX_SIDE = 1600       # enough to read small print; keeps checker prompts bounded


def page_count(data: bytes, media_type: str) -> int:
    if media_type == "application/pdf":
        import pypdfium2 as pdfium
        return len(pdfium.PdfDocument(data))
    if media_type.startswith("image"):
        with Image.open(io.BytesIO(data)) as im:
            return getattr(im, "n_frames", 1)
    return 0


def render_png(data: bytes, media_type: str, page: int, dpi: int = 150) -> Optional[bytes]:
    """PNG bytes of 1-based `page`, or None when the document has no page images (text formats)."""
    if media_type == "application/pdf":
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(data)
        if not 1 <= page <= len(pdf):
            return None
        img = pdf[page - 1].render(scale=dpi / 72).to_pil()
    elif media_type.startswith("image"):
        src = Image.open(io.BytesIO(data))
        if not 1 <= page <= getattr(src, "n_frames", 1):
            return None
        src.seek(page - 1)
        img = src.copy()
    else:
        return None
    img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
