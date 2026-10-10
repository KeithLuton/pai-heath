"""Segmenter — MEDICAL_PRIMITIVES.md §2 (one statement per Float).

Splits a clinical document into sections, sentences, clauses and list items,
keeping exact character offsets into the original text so every statement can
be traced back (§2.2 provenance). It knows nothing about any particular record:
no expected strings, no IDs.

Levels:
    section   — "Label:" headers from a fixed vocabulary (HPI, ROS, PMH, Plan …)
    sentence  — split at . ; boundaries (decimals and abbreviations protected)
    clause    — split at ';' and contrast markers (though, but, however …),
                which also end negation scope
    item      — list items inside list clauses ("Denies a, b, or c"; Plan/Meds/
                PMH lists), so each gets its own Float

Statements are what the context classifier and the Float extractor consume.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# Header vocabulary -> canonical section. Headers are recognized only at the
# document start or right after a sentence boundary, and only from this list,
# so "Vitamin B12:" or "Ref:" are never mistaken for sections.
SECTION_LABELS = {
    "hpi": "HPI", "history of present illness": "HPI", "cc": "CC", "chief complaint": "CC",
    "ros": "ROS", "review of systems": "ROS",
    "pmh": "PMH", "past medical history": "PMH", "psh": "PSH", "past surgical history": "PSH",
    "fh": "FH", "family history": "FH", "sh": "SH", "social history": "SH",
    "meds": "MEDS", "medications": "MEDS", "current medications": "MEDS", "allergies": "ALLERGIES",
    "exam": "EXAM", "physical exam": "EXAM", "pe": "EXAM", "vitals": "VITALS",
    "labs": "LABS", "results": "RESULTS", "imaging": "IMAGING",
    "assessment and plan": "ASSESSMENT_PLAN", "a/p": "ASSESSMENT_PLAN", "assessment": "ASSESSMENT",
    "impression": "ASSESSMENT", "plan": "PLAN", "note": "NOTE",
    # Organ-system sub-headers (ROS / exam)
    "neuro": "SUB:NEURO", "neurologic": "SUB:NEURO", "cardiac": "SUB:CARDIAC", "cv": "SUB:CARDIAC",
    "resp": "SUB:RESP", "pulm": "SUB:RESP", "gi": "SUB:GI", "gu": "SUB:GU", "msk": "SUB:MSK",
    "skin": "SUB:SKIN", "psych": "SUB:PSYCH", "heent": "SUB:HEENT", "constitutional": "SUB:CONSTITUTIONAL",
}
_LABEL_ALT = "|".join(sorted((re.escape(k) for k in SECTION_LABELS), key=len, reverse=True))
HEADER_RE = re.compile(rf"(?:^|(?<=[.\n;])\s*)(?P<label>{_LABEL_ALT})\s*:\s*", re.I)

# Sections whose bodies are lists by convention.
LIST_SECTIONS = {"PLAN", "MEDS", "PMH", "PSH", "ALLERGIES", "FH"}

# Clause-initial triggers that make what follows a list.
LIST_TRIGGER_RE = re.compile(
    r"^(?P<trig>denies|denied|negative for|positive for|no|without|endorses|reports|complains of|c/o|plan(?: to)?)\b[:\s]*",
    re.I)

TITLES = ("dr", "mr", "mrs", "ms", "st", "prof")            # never end a sentence
ABBREVIATIONS = ("vs", "e.g", "i.e", "approx", "pt", "hx", "no", "mg", "fig", "etc")  # end one only before a capital
CONTRAST_RE = re.compile(r",?\s+\b(though|although|but|however|whereas)\b\s+", re.I)
ITEM_SEP_RE = re.compile(r"\s*,\s*(?:and\s+|or\s+)?|\s+(?:and|or)\s+", re.I)


@dataclass(frozen=True)
class Span:
    start: int
    end: int

    def text(self, doc: str) -> str:
        return doc[self.start:self.end]


@dataclass(frozen=True)
class Statement:
    doc_id: str
    section: str               # canonical section, or "UNLABELED"
    sub_section: Optional[str]
    span: Span                 # the statement itself (list item or whole clause)
    clause: Span               # the clause it belongs to (context-trigger scope)
    sentence: Span
    list_trigger: Optional[Span]  # e.g. "Denies" governing the whole list
    item_index: Optional[int]  # position in its list, None if not a list item

    def text(self, doc: str) -> str:
        return self.span.text(doc)


def _trim(doc: str, start: int, end: int) -> Tuple[int, int]:
    while start < end and doc[start] in " \t\r\n,;:":
        start += 1
    while end > start and doc[end - 1] in " \t\r\n,;:.":
        end -= 1
    return start, end


def sections(doc: str) -> List[Tuple[str, Optional[str], int, int]]:
    """(section, sub_section, body_start, body_end) in document order."""
    out: List[Tuple[str, Optional[str], int, int]] = []
    current, sub = "UNLABELED", None
    pos = 0
    for m in HEADER_RE.finditer(doc):
        if m.start("label") > pos:
            out.append((current, sub, pos, m.start("label")))
        canon = SECTION_LABELS[m.group("label").lower()]
        if canon.startswith("SUB:"):
            sub = canon[4:]
        else:
            current, sub = canon, None
        pos = m.end()
    out.append((current, sub, pos, len(doc)))
    return [s for s in out if _trim(doc, s[2], s[3])[0] < _trim(doc, s[2], s[3])[1]]


def sentences(doc: str, start: int, end: int) -> List[Span]:
    """Split at '.', '?', '!', newline — never inside decimals or after abbreviations."""
    spans: List[Span] = []
    s = start
    i = start
    while i < end:
        ch = doc[i]
        boundary = False
        if ch == "\n":
            boundary = True
        elif ch in ".?!":
            nxt = doc[i + 1] if i + 1 < end else ""
            prev_word = re.search(r"([A-Za-z.]+)$", doc[s:i])
            decimal = ch == "." and i > start and doc[i - 1].isdigit() and nxt.isdigit()
            word = prev_word.group(1).lower().rstrip(".") if prev_word else ""
            abbrev = ch == "." and (word in TITLES or (word in ABBREVIATIONS and nxt == " "
                                                       and i + 2 < end and doc[i + 2].islower()))
            if not decimal and not abbrev and (nxt == "" or nxt.isspace()):
                boundary = True
        if boundary:
            a, b = _trim(doc, s, i + (1 if ch == "?" else 0))
            if a < b:
                spans.append(Span(a, b))
            s = i + 1
        i += 1
    a, b = _trim(doc, s, end)
    if a < b:
        spans.append(Span(a, b))
    return spans


def clauses(doc: str, sent: Span) -> List[Span]:
    """Split at ';' and contrast markers. The marker belongs to neither clause."""
    pieces: List[Span] = []
    cursor = sent.start
    text = doc[sent.start:sent.end]
    cuts: List[Tuple[int, int]] = []
    for m in re.finditer(r";", text):
        cuts.append((m.start(), m.end()))
    for m in CONTRAST_RE.finditer(text):
        cuts.append((m.start(), m.end()))
    for a, b in sorted(cuts):
        x, y = _trim(doc, cursor, sent.start + a)
        if x < y:
            pieces.append(Span(x, y))
        cursor = max(cursor, sent.start + b)
    x, y = _trim(doc, cursor, sent.end)
    if x < y:
        pieces.append(Span(x, y))
    return pieces


def _items(doc: str, clause: Span, section: str) -> Tuple[Optional[Span], List[Span]]:
    """Return (list trigger span, item spans). No split when the clause isn't a list."""
    text = doc[clause.start:clause.end]
    trig = LIST_TRIGGER_RE.match(text)
    if not trig and section not in LIST_SECTIONS:
        return None, [clause]
    body_start = clause.start + (trig.end() if trig else 0)
    trig_span = Span(clause.start, clause.start + len(trig.group("trig"))) if trig else None
    body = doc[body_start:clause.end]
    if not ITEM_SEP_RE.search(body):
        a, b = _trim(doc, body_start, clause.end)
        return trig_span, [Span(a, b)] if a < b else [clause]
    out: List[Span] = []
    cursor = 0
    for m in ITEM_SEP_RE.finditer(body):
        a, b = _trim(doc, body_start + cursor, body_start + m.start())
        if a < b:
            out.append(Span(a, b))
        cursor = m.end()
    a, b = _trim(doc, body_start + cursor, clause.end)
    if a < b:
        out.append(Span(a, b))
    return trig_span, out


def segment(doc_id: str, doc: str) -> List[Statement]:
    out: List[Statement] = []
    for section, sub, a, b in sections(doc):
        for sent in sentences(doc, a, b):
            for cl in clauses(doc, sent):
                trig, items = _items(doc, cl, section)
                listy = len(items) > 1 or trig is not None
                for i, it in enumerate(items):
                    out.append(Statement(doc_id, section, sub, it, cl, sent, trig, i if listy else None))
    return out
