"""PDF and image formats.

PDF: each page's text layer first. A page whose text layer is missing or fails
the quality gate is rendered (300 dpi) and OCR'd; the page records which method
produced its text. OCR text starts UNVERIFIED_EXTRACTION (§3.1).

Images: OCR with Tesseract, EXIF orientation applied, multi-frame TIFF (faxes)
as multiple pages. HEIC needs pillow-heif; without it the user is told to
export as JPEG.
"""
from __future__ import annotations

import io
from typing import List, Optional, Tuple

from .formats_text import Extract, normalize

OCR_DPI = 300
MAX_PAGES = 2000


def _quality_ok(text: str) -> bool:
    t = text.strip()
    if len(t) < 20:
        return False
    alpha = sum(ch.isalpha() for ch in t) / len(t)
    return alpha >= 0.35 and t.count("\ufffd") / len(t) < 0.02


def ocr_image(img) -> Tuple[str, Optional[float]]:
    """OCR a PIL image. Returns (text, mean word confidence 0-100)."""
    import pytesseract
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(img) or img
    clean = io.BytesIO()                       # drop source-format state (TIFF tiles, EXIF) before OCR
    img.convert("L").save(clean, "PNG")
    clean.seek(0)
    img = Image.open(clean)
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, config="--psm 3")
    confs = [float(c) for c, w in zip(data["conf"], data["text"]) if w.strip() and float(c) >= 0]
    lines, last = [], None
    for blk, par, ln, word in zip(data["block_num"], data["par_num"], data["line_num"], data["text"]):
        if not word.strip():
            continue
        key = (blk, par, ln)
        if key != last:
            lines.append([])
            last = key
        lines[-1].append(word)
    text = "\n".join(" ".join(l) for l in lines)
    return normalize(text), (sum(confs) / len(confs) if confs else None)


def tesseract_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def pdf(data: bytes, password: Optional[str] = None, allow_ocr: bool = True) -> Extract:
    import pdfplumber
    from pypdf import PdfReader
    ex = Extract()
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(password or ""):
            raise ValueError("this PDF is password-protected; supply the password to import it")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"unreadable PDF ({type(exc).__name__})")
    try:
        doc = pdfplumber.open(io.BytesIO(data), password=password or "")
    except Exception as exc:
        raise ValueError(f"unreadable PDF ({type(exc).__name__})")
    with doc:
        meta = doc.metadata or {}
        if meta.get("Title"):
            ex.metadata["title"] = str(meta["Title"])
        if meta.get("CreationDate"):
            ex.metadata["file_created"] = str(meta["CreationDate"])
        if len(doc.pages) > MAX_PAGES:
            raise ValueError(f"PDF has {len(doc.pages)} pages; the limit is {MAX_PAGES}")
        ocr_pages = []
        for i, page in enumerate(doc.pages):
            text = normalize(page.extract_text() or "")
            if _quality_ok(text):
                ex.pages.append((text, "TEXT_LAYER", None))
            else:
                ex.pages.append((text, "TEXT_LAYER", None))
                ocr_pages.append(i)
    if ocr_pages:
        if not allow_ocr or not tesseract_available():
            ex.warnings.append(f"pages {', '.join(str(i + 1) for i in ocr_pages)} have little or no text layer and "
                               "OCR is unavailable; they were kept as-is and flagged")
        else:
            import pypdfium2 as pdfium
            pdf_doc = pdfium.PdfDocument(data, password=password)
            for i in ocr_pages:
                img = pdf_doc[i].render(scale=OCR_DPI / 72).to_pil()
                text, conf = ocr_image(img)
                old = ex.pages[i][0]
                if len(text) > len(old):
                    ex.pages[i] = (text, "OCR", conf)
            ex.warnings.append(f"OCR used on page(s) {', '.join(str(i + 1) for i in ocr_pages)}")
    return ex


def image(data: bytes, allow_ocr: bool = True) -> Extract:
    from PIL import Image, ImageSequence
    try:
        img = Image.open(io.BytesIO(data))
    except Exception:
        if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftyphevc"):
            raise ValueError("HEIC photo: install HEIC support or export the photo as JPEG")
        raise ValueError("unreadable image")
    if not allow_ocr or not tesseract_available():
        raise ValueError("image needs OCR, which isn't available on this computer")
    ex = Extract()
    frames = list(ImageSequence.Iterator(img))
    if len(frames) > MAX_PAGES:
        raise ValueError(f"image has {len(frames)} frames; the limit is {MAX_PAGES}")
    for frame in frames:
        text, conf = ocr_image(frame.convert("RGB"))
        ex.pages.append((text, "OCR", conf))
    if len(frames) > 1:
        ex.warnings.append(f"multi-page image ({len(frames)} pages)")
    return ex
