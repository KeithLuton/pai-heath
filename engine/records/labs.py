"""Lab-report extractor for line-structured result documents.

Ported from the retired extract_labs.py (its layout handling was good) with
the §2.5 fixes applied:

* A unit not printed stays null. No default units.
* A reference range without a printed unit has reference_unit null; it is
  compared to the value as printed, and the comparison says so.
* Concepts carry no specimen the report doesn't state ("glucose", not
  "glucose, serum").
* A printed lab flag (H, L, CRITICAL, *) is its own INTERPRETATION Float
  (§2.4 rule 7), in addition to being recorded on the value.
* A collection date on a "Collected:" line is DOCUMENTED; a bare report
  date is an upper bound (DOCUMENT_DATE_FALLBACK).

Handles: Quest/Labcorp column layouts, flags before or after the unit,
censored values (<0.01, >2000), qualitative results (NEGATIVE, NOT DETECTED),
several collection blocks in one report, panel headings, page offsets.
Every Float satisfies doc.text[char_start:char_end] == original_text.
"""
from __future__ import annotations

import hashlib
import re
from typing import List, Optional, Tuple

from . import dates, lexicon

UNIT = (r"(?:mg/dL|mmol/L|pg/mL|pmol/L|mL/min(?:/[0-9.]+m2)?|%|g/dL|uIU/mL|mIU/L|IU/L|U/L|ng/mL|ng/dL|ug/dL|"
        r"mcg/dL|umol/L|nmol/L|K/uL|M/uL|thou/uL|cells/uL)")
FLAG = r"(?:HH|LL|H|L|HIGH|LOW|CRITICAL|ALERT|ABNORMAL|A|\*)"
QUAL = r"(?:NEGATIVE|POSITIVE|NOT\s+DETECTED|DETECTED|NON-REACTIVE|REACTIVE|NORMAL|CLEAR|TRACE)"
REF = rf"(?:[><]=?\s*\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s*[-–—]\s*\d+(?:[.,]\d+)?|{QUAL})"

RESULT_RE = re.compile(
    rf"(?:(?P<comp>[><]=?)\s*)?(?P<num>\d+(?:\.\d+)?)(?![\d/])|(?P<qual>{QUAL})", re.I)
AFTER_RE = re.compile(
    rf"^(?:\s+(?P<flag1>{FLAG})(?![A-Za-z]))?(?:\s*(?P<unit>{UNIT})(?![A-Za-z]))?(?:\s+(?P<flag2>{FLAG})(?![A-Za-z]))?"
    rf"(?:\s*\(?\s*(?:Ref(?:erence)?(?:\s+range)?|Range|Normal)?\s*:?\s*(?P<ref>{REF})\s*(?P<runit>{UNIT})?\s*\)?)?",
    re.I)
COLLECTED_RE = re.compile(r"\b(?:collected|collection(?:\s+date)?|specimen\s+date|drawn|date/time)\s*:?\s*"
                          r"(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})", re.I)
REPORT_DATE_RE = re.compile(r"\b(?:date|reported)\s*:?\s*(?P<date>\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})", re.I)
PANEL_RE = re.compile(r"^\s*(?:page\s*\d+\s*[—–-]\s*)?(?P<title>[A-Za-z0-9 /–—-]{3,50}(?:panel|profile|screen|count|urinalysis))\s*$", re.I)
NEGATIVE_QUAL = {"NEGATIVE", "NOT DETECTED", "NON-REACTIVE"}


def _iso(raw: str) -> str:
    raw = raw.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    mo, d, y = raw.split("/")
    y = int(y) + (2000 if len(y) == 2 else 0)
    return f"{y:04d}-{int(mo):02d}-{int(d):02d}"


def _ref_bounds(ref: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    s = ref.replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*[-–—]\s*(\d+(?:\.\d+)?)", s)
    if m:
        return float(m.group(1)), float(m.group(2)), None
    m = re.search(r"([><]=?)\s*(\d+(?:\.\d+)?)", s)
    if m:
        return (float(m.group(2)), None, None) if ">" in m.group(1) else (None, float(m.group(2)), None)
    m = re.search(QUAL, s, re.I)
    return (None, None, re.sub(r"\s+", " ", m.group(0).upper())) if m else (None, None, None)


def _compare(num, comp, qual, lo, hi, qref) -> Optional[str]:
    if num is not None:
        if qref in NEGATIVE_QUAL:
            return "WITHIN" if num == 0 else "ABOVE"
        if comp in ("<", "<=") and hi is not None and lo is None:
            return "WITHIN" if num <= hi else None          # censored upper bound inside the range
        if comp in (">", ">=") and lo is not None and hi is None:
            return "WITHIN" if num >= lo else None
        if comp and lo is not None and hi is not None:
            if comp in ("<", "<=") and num <= lo:
                return "BELOW"
            if comp in (">", ">=") and num >= hi:
                return "ABOVE"
            if comp in ("<", "<=") and num > lo:
                return None                                  # a bound, not a point: undeterminable
            if comp in (">", ">=") and num < hi:
                return None
        if lo is not None and hi is not None:
            return "BELOW" if num < lo else "ABOVE" if num > hi else "WITHIN"
        if lo is not None:
            return "WITHIN" if num >= lo else "BELOW"
        if hi is not None:
            return "WITHIN" if num <= hi else "ABOVE"
        return None
    if qual and qref:
        if qual == qref:
            return "WITHIN"
        if qual in ("POSITIVE", "DETECTED", "REACTIVE") and qref in NEGATIVE_QUAL | {"NORMAL"}:
            return "ABOVE"
        return "ABNORMAL"
    return None


def _num(s: str):
    v = float(s)
    return int(v) if v.is_integer() and "." not in s else v


def is_lab_report(text: str, title: str = "") -> bool:
    """Line-structured results: at least two lines that each start with an analyte followed by a result."""
    hits = 0
    for line in text.splitlines():
        m = lexicon.analyte_match(line)
        if m and m[1] <= 3 and RESULT_RE.search(line[m[2]:]):
            hits += 1
    return hits >= 2 and len([l for l in text.splitlines() if l.strip()]) >= 2


def extract(doc, page_of=None) -> Tuple[List[dict], List[dict]]:
    """Return (floats, notes) for a line-structured lab report RecordDoc."""
    text = doc.text
    floats: List[dict] = []
    notes: List[dict] = []
    lab_doc = True
    active_date: Optional[dates.EventDate] = None
    m = REPORT_DATE_RE.search(text)
    report_fallback = dates.fallback(_iso(m.group("date"))) if m else dates.fallback(doc.date)
    panel = "LABS"
    pos = 0
    for line in text.splitlines(keepends=True):
        start, pos = pos, pos + len(line)
        body = line.rstrip("\r\n")
        if not body.strip():
            continue
        cm = COLLECTED_RE.search(body)
        if cm:
            d = _iso(cm.group("date"))
            active_date = dates.EventDate(d, "DAY", "DOCUMENTED", meaning="collection date",
                                          lo=dates.date.fromisoformat(d), hi=dates.date.fromisoformat(d))
        pm = PANEL_RE.match(body)
        if pm and not lexicon.analyte_match(body):
            panel = pm.group("title").strip()
            continue
        am = lexicon.analyte_match(body)
        if not am or am[1] > 3 + (len(body) - len(body.lstrip())):
            continue
        canon, a_start, a_end = am
        # The name may run on ("GLUCOSE, FASTING", "EGFR NON-AFRICAN"): result is the first number/qualitative after it.
        rm = RESULT_RE.search(body, a_end)
        if not rm:
            continue
        tail = AFTER_RE.match(body[rm.end():])
        comp, qual = rm.group("comp"), rm.group("qual")
        num = _num(rm.group("num")) if rm.group("num") else None
        qual = re.sub(r"\s+", " ", qual.upper()) if qual else None
        unit = tail.group("unit") if tail else None
        flag = (tail.group("flag1") or tail.group("flag2")) if tail else None
        ref = tail.group("ref") if tail else None
        runit = tail.group("runit") if tail else None
        end_in_line = rm.end() + (tail.end() if tail else 0)
        # Unit and reference may sit in later columns ("105 H          mg/dL    65-99").
        if not unit:
            mu = re.match(rf"\s+(?:{FLAG}\s+)?(?P<unit>{UNIT})(?![A-Za-z])", body[end_in_line:], re.I)
            if mu:
                unit = mu.group("unit")
                flag = flag or (re.match(rf"\s+({FLAG})\s", body[end_in_line:], re.I) or [None, None])[1]
                end_in_line += mu.end()
        if not ref:
            mr = re.match(rf"\s+\(?\s*(?:Ref:?\s*)?(?P<ref>{REF})\s*(?P<runit>{UNIT})?\s*\)?", body[end_in_line:], re.I)
            if mr:
                ref, runit = mr.group("ref"), mr.group("runit")
                end_in_line += mr.end()
        lo, hi, qref = _ref_bounds(ref) if ref else (None, None, None)
        result = _compare(num, comp, qual, lo, hi, qref)
        a = start + a_start
        span = body[a_start:end_in_line].rstrip()
        if span.count("(") > span.count(")") and body[a_start + len(span):a_start + len(span) + 1] == ")":
            span += ")"
        b = a + len(span)
        assert text[a:b] == span
        evd = active_date or report_fallback
        page = page_of(a) if page_of else 1
        fid = hashlib.sha256(f"{doc.doc_id}|{a}|{b}|{canon}".encode()).hexdigest()[:12]
        value = {
            "analyte": canon, "numeric": num, "comparator": comp, "qualitative": qual, "unit": unit,
            "reference_low": lo, "reference_high": hi, "reference_qualitative": qref, "reference_unit": runit,
            "reported_flag": flag.upper() if flag else None, "specimen": None, "method": None,
            "performing_lab": None, "collected_at": evd.value if evd.source == "DOCUMENTED" else None,
            "lab_note": None, "primary_report_in_record_set": lab_doc,
        }
        base = {
            "concept": canon, "original_text": span, "speaker": "LAB_INSTRUMENT",
            "certainty": "DEFINITE", "temporality": "CURRENT", "experiencer": "PATIENT",
            "event_date": evd.as_dict(), "section": panel,
            "provenance": {"document_id": doc.doc_id, "document_sha256": doc.sha256, "page": page,
                           "char_start": a, "char_end": b, "author": None, "author_role": None,
                           "signed_date": None, "relay": "DIRECT"},
            "provenance_lock": "VERIFIED_SPAN", "content_status": "UNVERIFIED", "standing": "CLINICAL",
            "context_triggers": [], "review": [], "_date": evd, "_doc_date": doc.date,
        }
        f = dict(base, id="F-" + fid, kind="MEASUREMENT", root="R-" + fid,
                 polarity="NEGATED" if qual in NEGATIVE_QUAL else "AFFIRMED", value=value)
        if result:
            f["range_comparison"] = {"result": result, "basis": "ENGINE_COMPUTED"}
            if ref and not runit and num is not None:
                f["range_comparison"]["note"] = "reference range printed without a unit; compared as printed"
        if unit is None and num is not None:
            f["note"] = "No unit printed for this result; none assumed (§2.5)."
        floats.append(f)
        if flag:
            iid = hashlib.sha256(f"{doc.doc_id}|{a}|{b}|flag".encode()).hexdigest()[:12]
            floats.append(dict(base, id="F-" + iid, kind="INTERPRETATION", root="R-" + iid, polarity="AFFIRMED",
                               concept=f"{canon} flagged {flag.upper()} by the lab",
                               note="The lab's own flag (§2.4 rule 7); separate from PAi's range comparison."))
    return floats, notes
