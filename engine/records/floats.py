"""Float extractor — MEDICAL_PRIMITIVES.md §2, §4.

Statements (segment.py) + context axes (context.py) -> Floats, by general
rules over section, cue words and span shape. No record-specific strings.

Kind / speaker (first rule that applies):
  measurement pattern ("X = 180 pg/mL (Ref …)")  -> MEASUREMENT, LAB_INSTRUMENT
  EXAM section                                    -> OBSERVATION, CLINICIAN
  ASSESSMENT / IMPRESSION / PMH / PSH             -> ASSESSMENT, CLINICIAN
  MEDS section                                    -> MEDICATION_STATUS LISTED, CLINICIAN (note author)
  PLAN + medication verb + drug                   -> MEDICATION_STATUS *_PLANNED
  PLAN (anything else)                            -> ACTION, action_state from wording (default PLANNED)
  attribution ("patient reports …") or a ROS-style
  list trigger ("Denies …", "Negative for …")     -> REPORT, PATIENT
  HPI / CC sections                               -> REPORT, PATIENT
  FH section                                      -> REPORT, PATIENT (experiencer FAMILY from context)
  anything else                                   -> REPORT, UNKNOWN speaker + review note

Dates (§2.4 rule 10): a date expression in the statement wins (precision as
stated); measurements and medication-list entries take DOCUMENT_DATE_FALLBACK;
history-list entries without a date are UNKNOWN; everything else about the
encounter takes ENCOUNTER_DATE.

Never strengthened (§2.5): reference units not printed stay null, specimens
not printed stay null, performing lab stays null, medication start stays
UNKNOWN, list presence stays LISTED.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import dates, labs, lexicon
from .context import classify
from .segment import Statement, segment

MEAS_RE = re.compile(
    r"^(?P<analyte>[A-Za-z][A-Za-z0-9 ,\-()]*?)\s*(?:=|:|was|is|of)\s*(?P<cmp>[<>]=?)?\s*(?P<value>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>(?:[a-zA-Zµμ%]+(?:/[a-zA-Z0-9]+)?))?"
    r"(?:\s*\((?:ref(?:erence)?(?: range)?|normal|nl)\s*:?\s*(?P<lo>\d+(?:\.\d+)?)\s*[-–]\s*(?P<hi>\d+(?:\.\d+)?)"
    r"\s*(?P<runit>[a-zA-Zµμ%]+(?:/[a-zA-Z0-9]+)?)?\s*\))?",
    re.I)
SLASH_ITEM = re.compile(r"^(?P<a>[A-Za-z]{3,})/(?P<b>[A-Za-z]{3,})\s+(?P<rest>[A-Za-z].*)$")
MIN_COPY_TOKENS = 8              # [{calibrated}] §4.2 minimum span for copy-forward
NEAR_DUP_JACCARD = 0.9           # [{calibrated}] near-duplicate threshold on 3-token shingles


@dataclass
class RecordDoc:
    doc_id: str
    title: str
    date: Optional[str]          # ISO date of the document, if known
    text: str                    # canonical text (from ingestion)
    sha256: str
    page_locks: List[Tuple[int, int, str]] = field(default_factory=list)   # (start, end, initial lock)


def _lock_for(doc: RecordDoc, start: int, end: int) -> str:
    for a, b, lock in doc.page_locks:
        if a <= start and end <= b:
            return lock
    return "VERIFIED_SPAN" if not doc.page_locks else "UNVERIFIED_EXTRACTION"


def _id(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()[:12]


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _concept(text: str) -> str:
    t = lexicon.ATTRIBUTION_RE.sub("", text)
    t = lexicon.HEDGE_PREFIX.sub("", t)
    t = lexicon.DIFFERENTIAL_SUFFIX.sub("", t)
    t = lexicon.ONSET_PREFIX.sub("", t)
    t = re.sub(r"\s+(?:starting|since|beginning|began)\b.*$", "", t, flags=re.I)
    t = dates.DATE_EXPR.sub("", t)
    t = re.sub(r"\s+", " ", t).strip(" ,.;:'\"")
    # Lowercase ordinary words; keep acronyms and alphanumeric codes (B12, MMA, ACE, HbA1c).
    def word(w: str) -> str:
        letters = [c for c in w if c.isalpha()]
        keep = sum(c.isupper() for c in letters) >= 2 or (any(c.isdigit() for c in w) and any(c.isalpha() for c in w))
        return w if keep else w.lower()
    return " ".join(word(w) for w in t.split(" "))


def _measurement(text: str) -> Optional[dict]:
    m = MEAS_RE.match(text)
    if not m or not lexicon.analyte_of(m.group("analyte")):
        return None
    lo, hi = m.group("lo"), m.group("hi")
    return {
        "analyte": lexicon.analyte_of(m.group("analyte")),
        "numeric": float(m.group("value")) if "." in m.group("value") else int(m.group("value")),
        "comparator": m.group("cmp"),
        "unit": m.group("unit"),
        "reference_low": (float(lo) if "." in lo else int(lo)) if lo else None,
        "reference_high": (float(hi) if "." in hi else int(hi)) if hi else None,
        "reference_unit": m.group("runit"),                 # null when not printed (§2.5)
        "specimen": None, "method": None, "performing_lab": None, "collected_at": None, "lab_note": None,
    }


def _medication(text: str, status: str) -> Optional[dict]:
    drug = lexicon.drug_of(text)
    if not drug:
        return None
    dose = re.search(r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|units?|ml|iu)\b", text, re.I)
    freq = re.search(lexicon.FREQUENCIES, text, re.I)
    return {"drug": drug, "dose_text": dose.group(0) if dose else None, "route_text": None,
            "frequency_text": freq.group(0) if freq else None, "med_status": status, "start_date": "UNKNOWN"}


def _kind(doc: RecordDoc, st: Statement, text: str, ctx) -> Tuple[str, str, dict, List[str]]:
    """(kind, speaker, extras, notes)"""
    extras: dict = {}
    notes: List[str] = []
    meas = _measurement(text)
    if meas:
        lab_doc = bool(lexicon.LAB_DOC_TITLE.search(doc.title or ""))
        meas["primary_report_in_record_set"] = lab_doc
        extras["value"] = meas
        extras["relay"] = "DIRECT" if lab_doc else "TRANSCRIBED"
        return "MEASUREMENT", "LAB_INSTRUMENT", extras, notes
    sec = st.section
    if sec == "EXAM":
        return "OBSERVATION", "CLINICIAN", extras, notes
    if sec in ("ASSESSMENT", "ASSESSMENT_PLAN", "PMH"):
        return "ASSESSMENT", "CLINICIAN", extras, notes
    if sec == "PSH":
        extras["action_state"] = "PERFORMED"
        return "ACTION", "CLINICIAN", extras, notes
    if sec == "MEDS":
        med = _medication(text, "LISTED") or {"drug": _concept(text), "dose_text": None, "route_text": None,
                                              "frequency_text": None, "med_status": "LISTED", "start_date": "UNKNOWN"}
        extras["medication"] = med
        notes.append("LISTED only: not started, taken, prescribed or administered at this encounter (§2.3a).")
        return "MEDICATION_STATUS", "CLINICIAN", extras, notes
    if sec == "PLAN":
        for verb_re, status in lexicon.MED_PLAN_VERBS:
            if verb_re.match(text) and lexicon.drug_of(text):
                extras["medication"] = _medication(text, status)
                return "MEDICATION_STATUS", "CLINICIAN", extras, notes
        state = "PLANNED"
        for rx, s in lexicon.ACTION_STATE_POST:
            if rx.search(text):
                state = s
        extras["action_state"] = state
        if state == "PLANNED":
            notes.append("A plan, not an order and not a result (§2.5).")
        return "ACTION", "CLINICIAN", extras, notes
    attributed = bool(lexicon.ATTRIBUTION_RE.match(doc.text[st.sentence.start:st.sentence.end]))
    if attributed or st.list_trigger is not None or sec in ("HPI", "CC", "ROS", "FH") or sec.startswith("SUB"):
        return "REPORT", "PATIENT", extras, notes
    notes.append("speaker not established by section or wording")
    return "REPORT", "UNKNOWN", extras, notes


def _event_date(doc: RecordDoc, kind: str, text: str, ctx, extras: dict) -> dates.EventDate:
    found = dates.parse_expression(text) if kind not in ("MEASUREMENT",) else None
    if found:
        return found
    if kind == "MEASUREMENT" or (kind == "MEDICATION_STATUS" and extras.get("medication", {}).get("med_status") == "LISTED"):
        return dates.fallback(doc.date)
    if ctx.temporality in ("UNSPECIFIED", "HISTORICAL"):
        return dates.unknown()
    return dates.encounter(doc.date)


def _span_for(doc: RecordDoc, st: Statement) -> Tuple[int, int]:
    """Drop a leading attribution phrase ("Patient reports ") from the Float span."""
    a, b = st.span.start, st.span.end
    m = lexicon.ATTRIBUTION_RE.match(doc.text[a:b])
    if m:
        a += m.end()
    return a, b


def extract(docs: List[RecordDoc]) -> Tuple[List[dict], List[dict]]:
    """Return (floats, unextracted). Documents are processed oldest first so roots point to the earliest occurrence."""
    floats: List[dict] = []
    unextracted: List[dict] = []
    ordered = sorted(docs, key=lambda d: (d.date or "9999", d.doc_id))
    root_registry: List[Tuple[List[str], str, str]] = []   # (tokens, root, doc_id)
    for doc in ordered:
        doc_floats: List[dict] = []
        notes_for_doc: List[str] = []
        if labs.is_lab_report(doc.text, doc.title):
            # Line-structured result report: the lab extractor reads it; other lines are kept as notes.
            lab_floats, _ = labs.extract(doc)
            doc_floats.extend(lab_floats)
            covered = {(f["provenance"]["char_start"]) for f in lab_floats}
            pos = 0
            for line in doc.text.splitlines(keepends=True):
                start, pos = pos, pos + len(line)
                stripped = line.strip()
                lead = start + len(line) - len(line.lstrip())
                if stripped and not any(lead <= c < pos for c in covered):
                    unextracted.append({"document_id": doc.doc_id, "text": stripped,
                                        "reason": "line in a lab report that is not a result"})
            floats.extend(doc_floats)
            continue
        for st in segment(doc.doc_id, doc.text):
            text = st.text(doc.text)
            if st.section == "NOTE":
                notes_for_doc.append(text)
                continue
            ctx = classify(doc.text, st)
            kind, speaker, extras, notes = _kind(doc, st, text, ctx)
            a, b = _span_for(doc, st)
            span_text = doc.text[a:b]
            concepts = [_concept(span_text)]
            if kind == "MEASUREMENT":
                concepts = [extras["value"]["analyte"]]
            elif kind == "MEDICATION_STATUS":
                concepts = [extras["medication"]["drug"]]
            elif kind == "ACTION":
                stripped = lexicon.ACTION_VERBS.sub("", _concept(span_text))
                concepts = [lexicon.analyte_of(stripped) or stripped]
            else:
                sm = SLASH_ITEM.match(_concept(span_text))
                if sm and st.list_trigger is not None:
                    concepts = [f"{sm.group('a')} {sm.group('rest')}", f"{sm.group('b')} {sm.group('rest')}"]
            evd = _event_date(doc, kind, span_text, ctx, extras)
            tokens = _tokens(span_text)
            for concept in concepts:
                root = None
                if len(concepts) == 1 and len(tokens) >= MIN_COPY_TOKENS:
                    for prior_tokens, prior_root, prior_doc in root_registry:
                        if prior_doc != doc.doc_id and (prior_tokens == tokens or _jaccard(prior_tokens, tokens) >= NEAR_DUP_JACCARD):
                            root = prior_root
                            break
                copied = root is not None
                if root is None:
                    root = "R-" + _id(doc.doc_id, a, b, concept)
                    if len(concepts) == 1 and len(tokens) >= MIN_COPY_TOKENS:
                        root_registry.append((tokens, root, doc.doc_id))
                f = {
                    "id": "F-" + _id(doc.doc_id, a, b, concept),
                    "concept": concept, "original_text": span_text,
                    "speaker": speaker, "kind": kind,
                    "polarity": ctx.polarity, "certainty": ctx.certainty,
                    "temporality": ctx.temporality, "experiencer": ctx.experiencer,
                    "event_date": evd.as_dict(), "section": st.section,
                    "provenance": {"document_id": doc.doc_id, "document_sha256": doc.sha256, "page": 1,
                                   "char_start": a, "char_end": b, "author": None, "author_role": None,
                                   "signed_date": None, "relay": extras.get("relay", "DIRECT")},
                    "root": root,
                    "provenance_lock": _lock_for(doc, a, b) if doc.text[a:b] == span_text else "FAILED",
                    "content_status": "UNVERIFIED",
                    "standing": "CLINICAL",
                    "context_triggers": [{"axis": t.axis, "text": t.text, "start": t.start, "end": t.end} for t in ctx.triggers],
                    "review": list(ctx.review),
                    "_date": evd,
                    "_doc_date": doc.date,
                }
                if copied:
                    f["note"] = f"Copy-forward: {len(tokens)}-token span matches an earlier document; shares its root. Not a second source."
                if notes:
                    f["note"] = " ".join(([f["note"]] if f.get("note") else []) + notes)
                if len(concepts) > 1:
                    f["note"] = (f.get("note", "") + " Split from a combined list item; same span, separate statement.").strip()
                for key in ("medication", "action_state"):
                    if key in extras:
                        f[key] = extras[key]
                if "value" in extras:
                    v = dict(extras["value"])
                    f["value"] = v
                    if v["reference_low"] is not None and v["reference_high"] is not None:
                        num = v["numeric"]
                        res = "BELOW" if num < v["reference_low"] else "ABOVE" if num > v["reference_high"] else "WITHIN"
                        basis = "ENGINE_COMPUTED"
                        f["range_comparison"] = {"result": res, "basis": basis}
                        if v["reference_unit"] is None:
                            f["range_comparison"]["note"] = "reference range printed without a unit; compared as printed"
                doc_floats.append(f)
        if notes_for_doc:
            meas = [f for f in doc_floats if f["kind"] == "MEASUREMENT"]
            if meas:
                for f in meas:
                    f["value"]["lab_note"] = " ".join(notes_for_doc).rstrip(".") + "."
            else:
                for n in notes_for_doc:
                    unextracted.append({"document_id": doc.doc_id, "text": n, "reason": "note with no result to attach to"})
        floats.extend(doc_floats)
    return floats, unextracted


def _jaccard(a: List[str], b: List[str]) -> float:
    sa = {tuple(a[i:i + 3]) for i in range(max(1, len(a) - 2))}
    sb = {tuple(b[i:i + 3]) for i in range(max(1, len(b) - 2))}
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def public(f: dict) -> dict:
    return {k: v for k, v in f.items() if not k.startswith("_")}
