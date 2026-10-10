"""Ingestion output — what every format produces.

Canonical text is the text the rest of the engine reads and cites by
character offset. It is stored with the document, so a Float's span can be
re-checked forever. Pages are joined with a form feed so page boundaries are
recoverable from offsets.

Extraction method decides the starting provenance lock (§3.1):
    TEXT_LAYER, PLAIN, STRUCTURED, MARKUP -> spans can be VERIFIED_SPAN
    OCR                                   -> UNVERIFIED_EXTRACTION until the page
                                             checker or a person confirms the page
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

PAGE_SEP = "\f"
VERIFIABLE = {"TEXT_LAYER", "PLAIN", "STRUCTURED", "MARKUP"}


@dataclass
class PageQuality:
    chars: int
    alpha_ratio: float
    replacement_chars: int
    ocr_mean_confidence: Optional[float] = None
    flags: List[str] = field(default_factory=list)    # EMPTY, LOW_TEXT, GARBLED, LOW_OCR_CONFIDENCE


@dataclass
class IngestedPage:
    number: int                 # 1-based
    text: str
    method: str                 # TEXT_LAYER | OCR | PLAIN | MARKUP | STRUCTURED
    quality: PageQuality
    start: int = 0              # offset of this page in the canonical text
    end: int = 0

    @property
    def initial_lock(self) -> str:
        if self.quality.flags and set(self.quality.flags) & {"EMPTY", "GARBLED"}:
            return "UNVERIFIED_EXTRACTION"
        return "VERIFIED_SPAN" if self.method in VERIFIABLE else "UNVERIFIED_EXTRACTION"


@dataclass
class StructuredRecord:
    """A coded record from FHIR, C-CDA or HL7 v2, kept as exported.

    `pointer` locates it in the source (JSON pointer, XPath, segment index);
    `text_start/end` locate its rendered line in the canonical text.
    """
    kind: str                   # Observation | Medication | Condition | Allergy | Procedure | Immunization | Report
    display: str
    code_system: Optional[str] = None
    code: Optional[str] = None
    value: Optional[str] = None
    unit: Optional[str] = None
    reference_range: Optional[str] = None
    interpretation: Optional[str] = None        # source flag (H/L/abnormal) kept verbatim
    status: Optional[str] = None                # source status kept verbatim
    effective: Optional[str] = None             # source date string, verbatim
    pointer: str = ""
    text_start: int = 0
    text_end: int = 0


@dataclass
class IngestedDocument:
    doc_id: str                 # DOC- + sha256(original bytes)[:16]
    file_name: str
    media_type: str
    original_sha256: str
    canonical_text: str
    canonical_sha256: str
    pages: List[IngestedPage]
    structured: List[StructuredRecord] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)      # title, document date, author… only as found
    warnings: List[str] = field(default_factory=list)
    parent_id: Optional[str] = None                   # container (zip, email, FHIR attachment)


@dataclass
class IngestFailure:
    file_name: str
    reason: str
    parent_id: Optional[str] = None
