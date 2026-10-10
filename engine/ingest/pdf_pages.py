"""Rasterize medical PDFs (and passthrough images) to page PNGs.

Uses pypdfium2 for PDF pages — the same path that produces clean
input for OCR. Images are copied/normalized to PNG without upscaling.

Nothing here reads clinical content for meaning; embedded PDF text is only
counted (character total) so the engine knows whether a text layer exists.
"""
from __future__ import annotations

import hashlib
import io
import shutil
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from PIL import Image

from .schema import DocumentBundle, PageImage

# Default render DPI for lab/report PDFs. 200 is enough for OCR of printed
# text; 300 is safer for small-print reference ranges. Caller may override.
DEFAULT_DPI = 200
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}


class IngestError(ValueError):
    pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _media_type(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".bmp": "image/bmp",
    }.get(ext, "application/octet-stream")


def _write_png(img: Image.Image, dest: Path) -> Tuple[int, int, str]:
    """Save as PNG; return (width, height, sha256 of file bytes)."""
    # Normalize mode for OCR-friendly grayscale/RGB
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    dest.write_bytes(data)
    return img.width, img.height, _sha256_bytes(data)


def _count_pdf_text_chars(path: Path) -> int:
    """Best-effort character count from the PDF text layer (0 for pure scans)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return 0
    try:
        reader = PdfReader(str(path))
        total = 0
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            total += len(t)
        return total
    except Exception:
        return 0


def rasterize_pdf(
    path: Path,
    out_dir: Path,
    *,
    dpi: int = DEFAULT_DPI,
    first_page: Optional[int] = None,
    last_page: Optional[int] = None,
    fmt: str = "png",
) -> List[PageImage]:
    """Render PDF pages to `out_dir` / page_0001.png … (pypdfium2: Apache-2/BSD, no Poppler needed)."""
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise IngestError("pypdfium2 is required to rasterize PDFs") from exc

    out_dir.mkdir(parents=True, exist_ok=True)
    images = []
    try:
        pdf = pdfium.PdfDocument(str(path))
        try:
            n = len(pdf)
            start = (first_page or 1) - 1
            end = min(last_page or n, n)
            for i in range(start, end):
                page = pdf[i]
                try:
                    bitmap = page.render(scale=dpi / 72.0)
                    images.append(bitmap.to_pil().convert("RGB"))
                finally:
                    page.close()
        finally:
            pdf.close()
    except Exception as exc:
        raise IngestError(f"PDF rasterization failed: {exc}") from exc

    if not images:
        raise IngestError("PDF produced no pages")

    pages: List[PageImage] = []
    for i, img in enumerate(images):
        page_index = (first_page - 1 if first_page else 0) + i
        dest = out_dir / f"page_{page_index + 1:04d}.png"
        w, h, sha = _write_png(img, dest)
        pages.append(PageImage(
            page_index=page_index,
            path=str(dest.resolve()),
            width_px=w,
            height_px=h,
            dpi=dpi,
            sha256=sha,
            source="pdf_raster",
        ))
    return pages


def passthrough_image(path: Path, out_dir: Path) -> List[PageImage]:
    """Treat a single image file as page 0; normalize to PNG in out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(path) as img:
            img.load()
            dest = out_dir / "page_0001.png"
            w, h, sha = _write_png(img, dest)
    except Exception as exc:
        raise IngestError(f"could not open image {path.name}: {exc}") from exc
    return [PageImage(
        page_index=0,
        path=str(dest.resolve()),
        width_px=w,
        height_px=h,
        dpi=0,  # unknown native DPI unless EXIF; do not invent
        sha256=sha,
        source="image_passthrough",
    )]


def ingest_file(
    path: str | Path,
    out_dir: str | Path,
    *,
    dpi: int = DEFAULT_DPI,
    first_page: Optional[int] = None,
    last_page: Optional[int] = None,
) -> DocumentBundle:
    """Ingest one local PDF or image into page PNGs under out_dir."""
    path = Path(path)
    if not path.is_file():
        raise IngestError(f"not a file: {path}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sha = _sha256_file(path)
    media = _media_type(path)
    notes: List[str] = []

    if path.suffix.lower() == ".pdf" or media == "application/pdf":
        # Guard: refuse if not actually a PDF
        head = path.read_bytes()[:8]
        if not head.startswith(b"%PDF"):
            raise IngestError(f"{path.name} is not a PDF (magic {head!r})")
        pages = rasterize_pdf(path, out_dir, dpi=dpi, first_page=first_page, last_page=last_page)
        text_chars = _count_pdf_text_chars(path)
        if text_chars == 0:
            notes.append("No extractable text layer (likely a scan); OCR will be required.")
        else:
            notes.append(f"Embedded text layer present (~{text_chars} characters).")
    elif path.suffix.lower() in IMAGE_EXTENSIONS:
        pages = passthrough_image(path, out_dir)
        text_chars = 0
        notes.append("Image input; treated as a single page (no PDF text layer).")
    else:
        raise IngestError(
            f"unsupported file type {path.suffix!r}; use PDF or image "
            f"({', '.join(sorted(IMAGE_EXTENSIONS))})"
        )

    return DocumentBundle(
        document_id=sha,
        original_path=str(path.resolve()),
        original_sha256=sha,
        media_type=media if media != "application/octet-stream" else (
            "application/pdf" if path.suffix.lower() == ".pdf" else media
        ),
        page_count=len(pages),
        pages=pages,
        embedded_text_chars=text_chars,
        notes=notes,
    )


def iter_page_paths(bundle: DocumentBundle) -> Iterator[str]:
    for p in bundle.pages:
        yield p.path
