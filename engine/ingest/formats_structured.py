"""Structured clinical formats: FHIR (JSON), C-CDA (XML), HL7 v2 (pipe-delimited).

Coded records are kept as exported — codes, units, statuses, source flags and
date strings verbatim — and also rendered into canonical text, one line per
record, so the segmenter and Float extractor read them like any document while
each Float can still cite the exact source element (JSON pointer / XPath /
segment index). Nothing here interprets a status: "provisional",
"differential" or moodCode INT are carried through for the Float rules.

Embedded attachments (FHIR presentedForm / DocumentReference content, base64)
are returned as children and ingested as their own documents.
"""
from __future__ import annotations

import base64
import json
from typing import Any, List, Optional, Tuple

from .formats_text import Extract, html_to_text
from .model import StructuredRecord


class _Renderer:
    def __init__(self):
        self.lines: List[str] = []
        self.pos = 0
        self.records: List[StructuredRecord] = []

    def heading(self, text: str):
        self._add(text)

    def record(self, rec: StructuredRecord):
        parts = [rec.display]
        if rec.value is not None:
            parts.append(f": {rec.value}" + (f" {rec.unit}" if rec.unit else ""))
        if rec.reference_range:
            parts.append(f" (Ref: {rec.reference_range})")
        if rec.interpretation:
            parts.append(f" [flag: {rec.interpretation}]")
        if rec.status:
            parts.append(f" [status: {rec.status}]")
        if rec.effective:
            parts.append(f" [date: {rec.effective}]")
        line = "".join(parts)
        rec.text_start = self.pos
        rec.text_end = self.pos + len(line)
        self._add(line)
        self.records.append(rec)

    def _add(self, line: str):
        self.lines.append(line)
        self.pos += len(line) + 1

    def text(self) -> str:
        return "\n".join(self.lines)


# ====================================================================== FHIR

def _coding(cc: Any) -> Tuple[Optional[str], Optional[str], str]:
    if not isinstance(cc, dict):
        return None, None, ""
    codings = cc.get("coding") or []
    first = codings[0] if codings else {}
    display = cc.get("text") or first.get("display") or first.get("code") or ""
    return first.get("system"), first.get("code"), display


def _quantity(q: dict) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(q, dict) or q.get("value") is None:
        return None, None
    comparator = q.get("comparator") or ""
    return f"{comparator}{q['value']}", q.get("unit") or q.get("code")


def _range(rr: List[dict]) -> Optional[str]:
    if not rr:
        return None
    r = rr[0]
    if r.get("text"):
        return r["text"]
    low, high = r.get("low", {}), r.get("high", {})
    lo = f"{low.get('value')}" if low.get("value") is not None else ""
    hi = f"{high.get('value')}" if high.get("value") is not None else ""
    unit = high.get("unit") or low.get("unit") or ""
    return f"{lo}-{hi} {unit}".strip() if (lo or hi) else None


def _effective(res: dict, *keys: str) -> Optional[str]:
    for k in keys:
        v = res.get(k)
        if isinstance(v, str):
            return v
        if isinstance(v, dict) and (v.get("start") or v.get("end")):
            return f"{v.get('start', '?')}/{v.get('end', '?')}"
    return None


def fhir(data: bytes) -> Extract:
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("not valid JSON")
    if not isinstance(doc, dict) or "resourceType" not in doc:
        raise ValueError("JSON is not a FHIR resource (no resourceType)")
    ex = Extract()
    r = _Renderer()
    unmapped: dict = {}
    if doc["resourceType"] == "Bundle":
        items = [((e or {}).get("resource") or {}, f"/entry/{i}/resource") for i, e in enumerate(doc.get("entry") or [])]
        ex.metadata["bundle_type"] = doc.get("type")
    else:
        items = [(doc, "")]
    for res, ptr in items:
        _fhir_resource(res, ptr, r, ex, unmapped)
    if unmapped:
        ex.warnings.append("FHIR resource types not imported: " + ", ".join(f"{k} ({v})" for k, v in sorted(unmapped.items())))
    ex.pages.append((r.text(), "STRUCTURED", None))
    ex.structured = r.records
    return ex


def _fhir_resource(res: dict, ptr: str, r: _Renderer, ex: Extract, unmapped: dict):
    rt = res.get("resourceType")
    if rt == "Observation":
        system, code, display = _coding(res.get("code"))
        interp = _coding((res.get("interpretation") or [None])[0])[2] or None
        base = dict(code_system=system, code=code, status=res.get("status"),
                    effective=_effective(res, "effectiveDateTime", "effectivePeriod", "issued"),
                    reference_range=_range(res.get("referenceRange") or []), interpretation=interp)
        if res.get("component"):
            for j, comp in enumerate(res["component"]):
                cs, cc, cd = _coding(comp.get("code"))
                val, unit = _quantity(comp.get("valueQuantity") or {})
                r.record(StructuredRecord(kind="Observation", display=f"{display}: {cd}" if display else cd,
                                          value=val or comp.get("valueString"), unit=unit,
                                          pointer=f"{ptr}/component/{j}",
                                          **{**base, "code_system": cs, "code": cc,
                                             "reference_range": _range(comp.get("referenceRange") or []) or base["reference_range"]}))
            return
        val, unit = _quantity(res.get("valueQuantity") or {})
        if val is None:
            val = res.get("valueString") or _coding(res.get("valueCodeableConcept"))[2] or None
        r.record(StructuredRecord(kind="Observation", display=display, value=val, unit=unit, pointer=ptr, **base))
    elif rt in ("MedicationStatement", "MedicationRequest", "MedicationAdministration"):
        _, _, med = _coding(res.get("medicationCodeableConcept"))
        if not med and isinstance(res.get("medicationReference"), dict):
            med = res["medicationReference"].get("display", "")
        dosage = res.get("dosage") or res.get("dosageInstruction") or []
        dose_text = dosage[0].get("text") if dosage and isinstance(dosage[0], dict) else None
        status = res.get("status")
        if rt == "MedicationRequest" and res.get("intent"):
            status = f"{status}; intent {res['intent']}"
        r.record(StructuredRecord(kind=f"Medication ({rt})", display=med or "medication (unnamed)",
                                  value=dose_text, status=status,
                                  effective=_effective(res, "effectiveDateTime", "effectivePeriod", "authoredOn", "dateAsserted"),
                                  pointer=ptr))
    elif rt == "Condition":
        system, code, display = _coding(res.get("code"))
        clinical = _coding(res.get("clinicalStatus"))[1]
        verification = _coding(res.get("verificationStatus"))[1]
        r.record(StructuredRecord(kind="Condition", display=display, code_system=system, code=code,
                                  status=f"clinical {clinical}; verification {verification}",
                                  effective=_effective(res, "onsetDateTime", "onsetPeriod", "recordedDate"),
                                  pointer=ptr))
    elif rt == "AllergyIntolerance":
        system, code, display = _coding(res.get("code"))
        r.record(StructuredRecord(kind="Allergy", display=display, code_system=system, code=code,
                                  status=_coding(res.get("verificationStatus"))[1],
                                  effective=_effective(res, "recordedDate"), pointer=ptr))
    elif rt in ("Procedure", "Immunization"):
        system, code, display = _coding(res.get("code") or res.get("vaccineCode"))
        r.record(StructuredRecord(kind=rt, display=display, code_system=system, code=code, status=res.get("status"),
                                  effective=_effective(res, "performedDateTime", "performedPeriod", "occurrenceDateTime"),
                                  pointer=ptr))
    elif rt in ("DiagnosticReport", "DocumentReference"):
        _, _, display = _coding(res.get("code") or res.get("type"))
        if res.get("conclusion"):
            r.record(StructuredRecord(kind="Report", display=display or rt, value=res["conclusion"],
                                      status=res.get("status"), effective=_effective(res, "effectiveDateTime", "date", "issued"),
                                      pointer=f"{ptr}/conclusion"))
        attachments = list(res.get("presentedForm") or []) + [c.get("attachment", {}) for c in res.get("content") or []]
        for j, att in enumerate(attachments):
            if att.get("data"):
                name = att.get("title") or f"{rt}{ptr.replace('/', '_')}_{j}"
                ext = {"application/pdf": ".pdf", "text/plain": ".txt", "text/html": ".html", "application/xml": ".xml",
                       "image/jpeg": ".jpg", "image/png": ".png"}.get(att.get("contentType", ""), "")
                ex.children.append((name if name.endswith(ext) else name + ext, base64.b64decode(att["data"])))
            elif att.get("url"):
                ex.warnings.append(f"{rt} at {ptr} links to {att['url']}; linked content is not fetched")
    elif rt == "Patient":
        ex.metadata["patient_resource_present"] = True        # identity is not imported here
    elif rt:
        unmapped[rt] = unmapped.get(rt, 0) + 1


# ====================================================================== C-CDA

NS = {"h": "urn:hl7-org:v3", "xsi": "http://www.w3.org/2001/XMLSchema-instance"}


def ccda(data: bytes) -> Extract:
    from defusedxml import ElementTree as DET
    try:
        root = DET.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except Exception as exc:
        raise ValueError(f"unreadable or unsafe XML ({type(exc).__name__})")
    if not root.tag.endswith("ClinicalDocument"):
        raise ValueError("XML is not a C-CDA ClinicalDocument")
    ex = Extract()
    r = _Renderer()
    title = root.find("h:title", NS)
    if title is not None and (title.text or "").strip():
        ex.metadata["title"] = title.text.strip()
        r.heading(title.text.strip())
    eff = root.find("h:effectiveTime", NS)
    if eff is not None and eff.get("value"):
        ex.metadata["document_date"] = eff.get("value")
    sections = root.findall(".//h:structuredBody/h:component/h:section", NS)
    for si, sec in enumerate(sections):
        st = sec.find("h:title", NS)
        sec_title = (st.text or "").strip() if st is not None else f"Section {si + 1}"
        r.heading(f"{sec_title}:")
        text_el = sec.find("h:text", NS)
        if text_el is not None:
            from xml.etree.ElementTree import tostring
            narrative = html_to_text(tostring(text_el, encoding="unicode").replace("ns0:", "").replace("h:", ""))
            for line in narrative.splitlines():
                if line.strip():
                    r.heading(line.strip())
        for ei, obs in enumerate(sec.findall(".//h:entry//h:observation", NS)):
            code = obs.find("h:code", NS)
            val = obs.find("h:value", NS)
            if code is None or val is None:
                continue
            xtype = val.get(f"{{{NS['xsi']}}}type", "")
            value = val.get("value") if xtype in ("PQ", "INT", "REAL") else (val.get("displayName") or val.text)
            et = obs.find("h:effectiveTime", NS)
            effective = (et.get("value") or (et.find("h:low", NS).get("value") if et is not None and et.find("h:low", NS) is not None else None)) if et is not None else None
            interp = obs.find("h:interpretationCode", NS)
            rng = obs.find(".//h:referenceRange//h:value", NS)
            rng_text = None
            if rng is not None:
                lo, hi = rng.find("h:low", NS), rng.find("h:high", NS)
                rng_text = f"{lo.get('value') if lo is not None else ''}-{hi.get('value') if hi is not None else ''} " \
                           f"{(hi if hi is not None else lo).get('unit', '') if (hi is not None or lo is not None) else ''}".strip()
            status = obs.find("h:statusCode", NS)
            r.record(StructuredRecord(
                kind="Observation", display=code.get("displayName") or code.get("code") or "observation",
                code_system=code.get("codeSystem"), code=code.get("code"), value=value, unit=val.get("unit"),
                reference_range=rng_text, interpretation=interp.get("code") if interp is not None else None,
                status=f"{status.get('code')}; moodCode {obs.get('moodCode')}" if status is not None else f"moodCode {obs.get('moodCode')}",
                effective=effective, pointer=f"section[{si}]/entry-observation[{ei}]"))
        for ei, sa in enumerate(sec.findall(".//h:entry//h:substanceAdministration", NS)):
            mat = sa.find(".//h:manufacturedMaterial/h:code", NS)
            name = (mat.get("displayName") if mat is not None else None) or "medication (unnamed)"
            dose = sa.find("h:doseQuantity", NS)
            status = sa.find("h:statusCode", NS)
            r.record(StructuredRecord(
                kind="Medication (C-CDA substanceAdministration)", display=name,
                code_system=mat.get("codeSystem") if mat is not None else None, code=mat.get("code") if mat is not None else None,
                value=dose.get("value") if dose is not None else None, unit=dose.get("unit") if dose is not None else None,
                status=f"{status.get('code') if status is not None else '?'}; moodCode {sa.get('moodCode')}",
                pointer=f"section[{si}]/entry-substanceAdministration[{ei}]"))
    ex.pages.append((r.text(), "STRUCTURED", None))
    ex.structured = r.records
    return ex


# ====================================================================== HL7 v2

def hl7v2(data: bytes) -> Extract:
    text = data.decode("utf-8", errors="replace")
    if not text.startswith("MSH"):
        raise ValueError("not an HL7 v2 message (no MSH segment)")
    field_sep = text[3]
    comp_sep = text[4] if len(text) > 4 else "^"
    segments = [s for s in text.replace("\r\n", "\r").replace("\n", "\r").split("\r") if s.strip()]
    ex = Extract()
    r = _Renderer()
    for idx, seg in enumerate(segments):
        f = seg.split(field_sep)
        kind = f[0]
        if kind == "OBR" and len(f) > 4:
            r.heading(f"{f[4].split(comp_sep)[1] if comp_sep in f[4] else f[4]}:")
        elif kind == "OBX" and len(f) > 5:
            code_parts = f[3].split(comp_sep) if len(f) > 3 else [""]
            get = lambda i: f[i] if len(f) > i and f[i] else None
            r.record(StructuredRecord(
                kind="Observation", display=code_parts[1] if len(code_parts) > 1 and code_parts[1] else code_parts[0],
                code=code_parts[0] or None, code_system=code_parts[2] if len(code_parts) > 2 and code_parts[2] else None,
                value=get(5), unit=(get(6) or "").split(comp_sep)[0] or None, reference_range=get(7),
                interpretation=get(8), status=get(11), effective=get(14), pointer=f"segment[{idx}] OBX"))
        elif kind == "NTE" and len(f) > 3 and f[3].strip():
            r.heading(f"Note: {f[3].strip()}")
        elif kind == "PID":
            ex.metadata["patient_segment_present"] = True     # identity is not imported here
    ex.pages.append((r.text(), "STRUCTURED", None))
    ex.structured = r.records
    return ex
