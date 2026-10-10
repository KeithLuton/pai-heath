"""Lines a layout extractor can read, each able to say exactly where a value came from.

Two sources:

* TEXT  — lines of the document's canonical text. Locator: page + text_start/text_end,
          and canonical_text[text_start:text_end] == raw_value.
* GEOM  — words with positions, from the PDF text layer (pdfplumber) or from OCR
          (Tesseract word boxes). Words are laid out into a fixed-pitch line so
          columns line up; a value's locator is page + bbox of its words, plus
          canonical offsets when the same text occurs exactly once on that page.

The canonical text of a PDF collapses column gaps to single spaces, which makes
claim and EOB tables unreadable by column; the word boxes keep them.
"""
from __future__ import annotations

import io
import re
import statistics
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

OCR_DPI = 300
LOW_WORD_CONF = 60.0


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    conf: Optional[float] = None


@dataclass
class Line:
    text: str
    page: int
    mode: str                                   # TEXT | GEOM | SRC
    base: Optional[int] = None                  # TEXT: canonical offset of text[0]
    words: List[Tuple[int, int, Word]] = field(default_factory=list)   # GEOM: (start, end, word) in text
    bbox_unit: str = "pt"
    page_text: str = ""                         # GEOM: canonical text of the page
    page_start: int = 0
    cmap: List[Optional[int]] = field(default_factory=list)   # SRC: canonical offset of each char (None = space)
    canon: str = ""

    def _span(self, a: int, b: int) -> Optional[Tuple[int, int]]:
        idx = [self.cmap[i] for i in range(a, min(b, len(self.cmap))) if self.cmap[i] is not None]
        return (idx[0], idx[-1] + 1) if idx else None

    def raw(self, a: int, b: int) -> str:
        if self.mode == "SRC":
            sp = self._span(a, b)
            return self.canon[sp[0]:sp[1]] if sp else ""
        s = self.text[a:b]
        return s if self.mode == "TEXT" else re.sub(r" {2,}", " ", s)

    def locate(self, a: int, b: int) -> Tuple[dict, List[str]]:
        issues: List[str] = []
        if self.mode == "TEXT":
            return {"page": self.page, "text_start": self.base + a, "text_end": self.base + b}, issues
        if self.mode == "SRC":
            sp = self._span(a, b) or (0, 0)
            return {"page": self.page, "text_start": sp[0], "text_end": sp[1]}, issues
        ws = [w for s, e, w in self.words if s < b and e > a]
        loc: dict = {"page": self.page}
        if ws:
            loc["bbox"] = [round(min(w.x0 for w in ws), 1), round(min(w.top for w in ws), 1),
                           round(max(w.x1 for w in ws), 1), round(max(w.bottom for w in ws), 1)]
            loc["bbox_unit"] = self.bbox_unit
            confs = [w.conf for w in ws if w.conf is not None]
            if confs:
                loc["ocr_word_confidence_min"] = round(min(confs), 1)
                if min(confs) < LOW_WORD_CONF:
                    issues.append("LOW_OCR_CONFIDENCE")
        raw = self.raw(a, b)
        if raw and self.page_text.count(raw) == 1:
            i = self.page_text.index(raw)
            loc["text_start"] = self.page_start + i
            loc["text_end"] = self.page_start + i + len(raw)
        return loc, issues


def text_lines(canonical: str, pages) -> List[Line]:
    out: List[Line] = []
    for p in pages:
        pos = p.start
        for raw in canonical[p.start:p.end].splitlines(keepends=True):
            body = raw.rstrip("\r\n")
            out.append(Line(body, p.number, "TEXT", base=pos))
            pos += len(raw)
    return out


_WS = " \t\u00a0\r\n\f"


def source_lines(doc, original: bytes) -> List[Line]:
    """Plain-text documents: read the original layout (column gaps intact) and map each
    character to its offset in the canonical text, which only differs in whitespace."""
    if doc.media_type != "text/plain" or not original or len(doc.pages) != 1:
        return []
    from ingest.formats_text import decode_text
    text, _ = decode_text(original)
    canon = doc.canonical_text
    targets = [j for j, ch in enumerate(canon) if ch not in _WS]
    out, k = [], 0
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n").split("\n"):
        cmap: List[Optional[int]] = []
        for ch in raw_line:
            if ch in _WS:
                cmap.append(None)
                continue
            if k >= len(targets) or canon[targets[k]] != ch:
                return []                        # not a whitespace-only difference: don't trust a mapping
            cmap.append(targets[k])
            k += 1
        out.append(Line(raw_line.replace("\t", " ").replace("\u00a0", " "), 1, "SRC", cmap=cmap, canon=canon))
    return out if k == len(targets) else []


def layout_words(words: List[Word], page: int, unit: str, page_text: str, page_start: int) -> List[Line]:
    """Group words into lines by vertical position and lay them out at fixed pitch."""
    if not words:
        return []
    widths = [(w.x1 - w.x0) / max(len(w.text), 1) for w in words if w.x1 > w.x0]
    cw = statistics.median(widths) if widths else 5.0
    heights = [w.bottom - w.top for w in words if w.bottom > w.top]
    tol = (statistics.median(heights) if heights else 8.0) * 0.5
    rows: List[List[Word]] = []
    for w in sorted(words, key=lambda w: ((w.top + w.bottom) / 2, w.x0)):
        mid = (w.top + w.bottom) / 2
        if rows and abs(mid - statistics.mean((x.top + x.bottom) / 2 for x in rows[-1])) <= tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    out = []
    tops = [min(w.top for w in r) for r in rows]
    steps = sorted(b - a for a, b in zip(tops, tops[1:]))
    step = steps[len(steps) // 2] if steps else 0
    for n, row in enumerate(rows):
        if n and step and tops[n] - tops[n - 1] > step * 1.8:
            out.append(Line("", page, "GEOM", bbox_unit=unit, page_text=page_text, page_start=page_start))
        row.sort(key=lambda w: w.x0)
        text, spans, prev = "", [], None
        for w in row:
            if prev is None:
                col = int(round(w.x0 / cw))
            elif w.x0 - prev.x1 < cw * 1.8:              # ordinary word space (monospace incl.)
                col = len(text) + 1
            else:
                col = max(len(text) + 2, int(round(w.x0 / cw)))
            text = text.ljust(col) + w.text
            spans.append((col, col + len(w.text), w))
            prev = w
        out.append(Line(text, page, "GEOM", words=spans, bbox_unit=unit, page_text=page_text, page_start=page_start))
    return out


def pdf_words(data: bytes, page_index: int) -> List[Word]:
    import pdfplumber
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        page = pdf.pages[page_index]
        return [Word(w["text"], w["x0"], w["x1"], w["top"], w["bottom"])
                for w in page.extract_words(keep_blank_chars=False, use_text_flow=False)]


def ocr_words(img) -> List[Word]:
    import pytesseract
    d = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT, config="--psm 6")
    out = []
    for i, t in enumerate(d["text"]):
        t = (t or "").strip()
        if not t:
            continue
        try:
            conf = float(d["conf"][i])
        except (TypeError, ValueError):
            conf = None
        out.append(Word(t, d["left"][i], d["left"][i] + d["width"][i], d["top"][i], d["top"][i] + d["height"][i],
                        conf if conf is not None and conf >= 0 else None))
    return out


def _tesseract() -> bool:
    try:
        from ingest.formats_pdf_image import tesseract_available
        return tesseract_available()
    except Exception:
        return False


def document_lines(doc, original: bytes) -> Tuple[List[Line], List[str]]:
    """Best available lines for each page, and notes on how each page was read."""
    notes: List[str] = []
    lines: List[Line] = []
    is_pdf = doc.media_type == "application/pdf" and original[:4] == b"%PDF"
    is_img = doc.media_type.startswith("image") and bool(original)
    frames = None
    src = source_lines(doc, original)
    if src:
        return src, ["read from the original text layout (column gaps kept)"]
    for p in doc.pages:
        page_text = doc.canonical_text[p.start:p.end]
        got: List[Line] = []
        try:
            if is_pdf and p.method == "TEXT_LAYER":
                got = layout_words(pdf_words(original, p.number - 1), p.number, "pt", page_text, p.start)
                if got:
                    notes.append(f"page {p.number}: word positions from the PDF text layer")
            elif (is_pdf or is_img) and p.method == "OCR" and _tesseract():
                if is_pdf:
                    import pypdfium2 as pdfium
                    img = pdfium.PdfDocument(original)[p.number - 1].render(scale=OCR_DPI / 72).to_pil()
                    words = [Word(w.text, w.x0 * 72 / OCR_DPI, w.x1 * 72 / OCR_DPI, w.top * 72 / OCR_DPI,
                                  w.bottom * 72 / OCR_DPI, w.conf) for w in ocr_words(img)]
                    got = layout_words(words, p.number, "pt", page_text, p.start)
                else:
                    from PIL import Image, ImageSequence
                    if frames is None:
                        frames = list(ImageSequence.Iterator(Image.open(io.BytesIO(original))))
                    got = layout_words(ocr_words(frames[p.number - 1].convert("RGB")), p.number, "px",
                                       page_text, p.start)
                if got:
                    notes.append(f"page {p.number}: word positions from OCR (Tesseract)")
        except Exception as exc:                       # fall back to text, never fail the document
            notes.append(f"page {p.number}: word positions unavailable ({type(exc).__name__}); read as text")
            got = []
        if not got:
            got = text_lines(doc.canonical_text, [p])
        lines += got
    return lines, notes


def acroform_values(original: bytes) -> int:
    """Count filled form fields; their values are not in the text layer."""
    if original[:4] != b"%PDF":
        return 0
    try:
        from pypdf import PdfReader
        fields = PdfReader(io.BytesIO(original)).get_fields() or {}
        return sum(1 for f in fields.values() if str(f.get("/V") or "").strip() not in ("", "/Off"))
    except Exception:
        return 0
