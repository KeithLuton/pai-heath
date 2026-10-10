"""Ingest any supported file into IngestedDocuments.

Detection is by content (magic bytes / structure), never by extension alone,
so a PDF named "scan.txt" is still read as a PDF. Containers (zip, email,
FHIR attachments) are opened and each member becomes its own document linked
to its parent. Anything unsupported comes back as an IngestFailure with a
plain reason and what to do instead — never silently skipped.

Limits guard against hostile files: zip bombs (member count, total size,
compression ratio), nesting depth, page counts, entity-laden XML.
"""
from __future__ import annotations

import email
import hashlib
import io
import zipfile
from email import policy
from pathlib import Path
from typing import List, Optional, Tuple

from . import formats_pdf_image as fpi
from . import formats_structured as fs
from . import formats_text as ft
from .model import PAGE_SEP, IngestedDocument, IngestedPage, IngestFailure, PageQuality

MAX_DEPTH = 3
MAX_ZIP_MEMBERS = 5000
MAX_ZIP_TOTAL = 2 * 1024 ** 3
MAX_ZIP_RATIO = 200
MAX_FILE = 1024 ** 3
LOW_OCR_CONF = 70.0

SUPPORTED = {
    "PDF (text or scanned)": "pdf",
    "Images: JPEG, PNG, TIFF incl. multi-page faxes, BMP, GIF, WebP (OCR)": "image",
    "Word .docx": "docx", "OpenDocument .odt": "odt", "RTF": "rtf",
    "Plain text / Markdown": "text", "HTML (saved portal pages)": "html",
    "Email .eml with attachments": "eml",
    "FHIR JSON (resource or Bundle; patient-portal downloads)": "fhir",
    "C-CDA / CCD XML (portal 'download my record', Apple Health export_cda.xml)": "ccda",
    "HL7 v2 lab messages": "hl7v2",
    "CSV and Excel .xlsx tables": "table",
    "ZIP archives of any of the above (e.g. Apple Health export.zip)": "zip",
}

UNSUPPORTED_HELP = {
    "ole_msg": "Outlook .msg isn't supported. In Outlook, use File > Save As > .eml, or print the message to PDF.",
    "ole_doc": "Old Word .doc / Excel .xls isn't supported. Save it as .docx/.xlsx or print to PDF.",
    "dicom": "DICOM is imaging pixel data, not a report. Import the radiologist's report (PDF or text) instead.",
    "heic": "HEIC photo: export it from Photos as JPEG, or install HEIC support.",
    "json": "This JSON isn't FHIR. If it came from a device app, use the device importers.",
    "xml": "This XML isn't a C-CDA document.",
    "binary": "Unrecognized binary file.",
}


def _sniff(data: bytes, name: str) -> str:
    head = data[:4096]
    lower_name = name.lower()
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        try:
            names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        except zipfile.BadZipFile:
            return "binary"
        if "word/document.xml" in names:
            return "docx"
        if "xl/workbook.xml" in names:
            return "xlsx"
        if "mimetype" in names and zipfile.ZipFile(io.BytesIO(data)).read("mimetype").startswith(b"application/vnd.oasis.opendocument.text"):
            return "odt"
        return "zip"
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return "ole_msg" if lower_name.endswith(".msg") else "ole_doc"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if head.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"II*\x00", b"MM\x00*", b"BM")) or \
            (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        return "image"
    if head[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftyphevc"):
        return "heic"
    if len(data) > 132 and data[128:132] == b"DICM":
        return "dicom"
    try:
        text = head.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = head.decode("latin-1")
        if sum(ch < " " and ch not in "\r\n\t\f" for ch in text) > len(text) * 0.05:
            return "binary"
    t = text.lstrip()
    if t.startswith("{") or t.startswith("["):
        return "fhir" if '"resourceType"' in text else "json"
    if t.startswith("MSH") and len(t) > 4:
        return "hl7v2"
    if t.startswith("<"):
        low = t[:2000].lower()
        if "clinicaldocument" in low:
            return "ccda"
        if "<html" in low or "<!doctype html" in low or "<body" in low:
            return "html"
        return "xml"
    first = "\n".join(t.splitlines()[:40]).lower()
    if lower_name.endswith(".eml") or (("\nfrom:" in "\n" + first or "\nreceived:" in "\n" + first)
                                       and ("\nsubject:" in "\n" + first or "\nmime-version:" in "\n" + first)):
        return "eml"
    if lower_name.endswith((".csv", ".tsv")):
        return "csv"
    return "text"


def _quality(text: str, method: str, conf: Optional[float]) -> PageQuality:
    stripped = text.strip()
    n = len(stripped)
    alpha = (sum(ch.isalpha() for ch in stripped) / n) if n else 0.0
    q = PageQuality(chars=n, alpha_ratio=round(alpha, 3), replacement_chars=stripped.count("\ufffd"),
                    ocr_mean_confidence=None if conf is None else round(conf, 1))
    if n == 0:
        q.flags.append("EMPTY")
    elif n < 20 and method != "STRUCTURED":
        q.flags.append("LOW_TEXT")
    if n >= 20 and (alpha < 0.35 or q.replacement_chars / n > 0.02) and method != "STRUCTURED":
        q.flags.append("GARBLED")
    if conf is not None and conf < LOW_OCR_CONF:
        q.flags.append("LOW_OCR_CONFIDENCE")
    return q


def _assemble(data: bytes, name: str, media: str, ex: ft.Extract, parent: Optional[str]) -> IngestedDocument:
    sha = hashlib.sha256(data).hexdigest()
    pages: List[IngestedPage] = []
    pos = 0
    texts = []
    for i, (text, method, conf) in enumerate(ex.pages or [("", "PLAIN", None)]):
        text = text.replace(PAGE_SEP, "\n")
        if i:
            pos += len(PAGE_SEP)
        pages.append(IngestedPage(number=i + 1, text=text, method=method, quality=_quality(text, method, conf),
                                  start=pos, end=pos + len(text)))
        texts.append(text)
        pos += len(text)
    canonical = PAGE_SEP.join(texts)
    for rec in ex.structured:                 # structured formats render on page 1
        rec.text_start += pages[0].start
        rec.text_end += pages[0].start
    for p in pages:
        for flag in p.quality.flags:
            ex.warnings.append(f"page {p.number}: {flag.replace('_', ' ').lower()}")
    return IngestedDocument(
        doc_id=f"DOC-{sha[:16]}", file_name=name, media_type=media, original_sha256=sha,
        canonical_text=canonical, canonical_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        pages=pages, structured=list(ex.structured), metadata=dict(ex.metadata),
        warnings=list(dict.fromkeys(ex.warnings)), parent_id=parent)


def _zip_children(data: bytes) -> List[Tuple[str, bytes]]:
    zf = zipfile.ZipFile(io.BytesIO(data))
    infos = [i for i in zf.infolist() if not i.is_dir() and not i.filename.startswith("__MACOSX/")
             and not i.filename.endswith(".DS_Store")]
    if len(infos) > MAX_ZIP_MEMBERS:
        raise ValueError(f"archive has {len(infos)} files; the limit is {MAX_ZIP_MEMBERS}")
    if sum(i.file_size for i in infos) > MAX_ZIP_TOTAL:
        raise ValueError("archive expands beyond 2 GB; refusing (possible zip bomb)")
    out = []
    for i in infos:
        if i.compress_size and i.file_size / i.compress_size > MAX_ZIP_RATIO and i.file_size > 10 * 1024 ** 2:
            raise ValueError(f"{i.filename} has a {i.file_size // i.compress_size}:1 compression ratio; refusing (possible zip bomb)")
        out.append((i.filename, zf.read(i)))
    return out


def _eml(data: bytes) -> Tuple[ft.Extract, List[Tuple[str, bytes]]]:
    msg = email.message_from_bytes(data, policy=policy.default)
    ex = ft.Extract()
    for key in ("subject", "date", "from"):
        if msg[key]:
            ex.metadata[key] = str(msg[key])
    body = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if body is not None:
        content = body.get_content()
        text = ft.html_to_text(content) if body.get_content_type() == "text/html" else ft.normalize(content)
    header = "\n".join(f"{k.title()}: {ex.metadata[k]}" for k in ("from", "date", "subject") if k in ex.metadata)
    ex.pages.append(((header + "\n\n" + text).strip(), "MARKUP", None))
    children = []
    for part in msg.iter_attachments():
        payload = part.get_payload(decode=True)
        if payload:
            children.append((part.get_filename() or f"attachment.{part.get_content_subtype()}", payload))
    return ex, children


def ingest_bytes(data: bytes, name: str, parent: Optional[str] = None, depth: int = 0,
                 password: Optional[str] = None, allow_ocr: bool = True
                 ) -> Tuple[List[IngestedDocument], List[IngestFailure]]:
    docs: List[IngestedDocument] = []
    fails: List[IngestFailure] = []
    if len(data) > MAX_FILE:
        return docs, [IngestFailure(name, "file is larger than 1 GB", parent)]
    if depth > MAX_DEPTH:
        return docs, [IngestFailure(name, "nested too deeply (containers within containers)", parent)]
    kind = _sniff(data, name)
    if kind in UNSUPPORTED_HELP:
        return docs, [IngestFailure(name, UNSUPPORTED_HELP[kind], parent)]
    try:
        children: List[Tuple[str, bytes]] = []
        if kind == "zip":
            sha = hashlib.sha256(data).hexdigest()
            container_id = f"DOC-{sha[:16]}"
            for child_name, child in _zip_children(data):
                d, f = ingest_bytes(child, f"{name}:{child_name}", container_id, depth + 1, None, allow_ocr)
                docs += d
                fails += f
            return docs, fails
        if kind == "eml":
            ex, children = _eml(data)
            media = "message/rfc822"
        else:
            handler, media = {
                "pdf": (lambda b: fpi.pdf(b, password, allow_ocr), "application/pdf"),
                "image": (lambda b: fpi.image(b, allow_ocr), "image"),
                "docx": (ft.docx, "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                "xlsx": (ft.xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
                "odt": (ft.odt, "application/vnd.oasis.opendocument.text"),
                "rtf": (ft.rtf, "application/rtf"),
                "html": (ft.html, "text/html"),
                "fhir": (fs.fhir, "application/fhir+json"),
                "ccda": (fs.ccda, "application/xml; C-CDA"),
                "hl7v2": (fs.hl7v2, "x-application/hl7-v2"),
                "csv": (ft.csv_table, "text/csv"),
                "text": (ft.plain, "text/plain"),
            }[kind]
            ex = handler(data)
            children = ex.children
        doc = _assemble(data, name, media, ex, parent)
        docs.append(doc)
        for child_name, child in children:
            d, f = ingest_bytes(child, f"{name}:{child_name}", doc.doc_id, depth + 1, None, allow_ocr)
            docs += d
            fails += f
    except ValueError as exc:
        fails.append(IngestFailure(name, str(exc), parent))
    except Exception as exc:                                  # never crash an import batch
        fails.append(IngestFailure(name, f"could not be read ({type(exc).__name__})", parent))
    return docs, fails


def ingest_path(path: str | Path, password: Optional[str] = None, allow_ocr: bool = True
                ) -> Tuple[List[IngestedDocument], List[IngestFailure]]:
    p = Path(path)
    return ingest_bytes(p.read_bytes(), p.name, None, 0, password, allow_ocr)
