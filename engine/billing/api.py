"""Entry points used by the app: classify a document, list profiles, extract.

Paper documents: `classify` returns candidate profiles from printed markers
(routing hints only). Extraction runs for a profile a person confirmed, or one
they picked when no marker matched. FHIR financial resources are structural:
the resourceType decides, so they are read without asking.
"""
from __future__ import annotations

import copy
import hashlib
import threading
from collections import OrderedDict
from typing import List, Optional

from ingest import billing_profiles as BP

from . import fhir as FH
from . import layout as LO
from . import layouts as L
from .lines import acroform_values, document_lines

ADAPTER_VERSION = "billing-1.0"
PAPER = list(L.LAYOUTS)
FHIR = list(FH.PROFILE_OF.values())
SUPPORTED = PAPER + FHIR
_ALIASES = {"subscriber": {"subscriber", "subscriber_id", "group"}, "provider": {"provider", "rendering_provider"},
            "total": {"total"}, "diagnosis": {"diagnosis"}}
_cache: "OrderedDict[str, dict]" = OrderedDict()
_cache_lock = threading.Lock()


def profiles() -> List[dict]:
    out = []
    for pid, p in BP.PROFILES.items():
        out.append({"profile_id": pid, "title": p["title"], "family": p["family"], "description": p["description"],
                    "supported": pid in SUPPORTED, "confirm": pid in PAPER,
                    "status": L.STATUS if pid in PAPER else FH.STATUS if pid in FHIR else "NO_ADAPTER"})
    return out


def _fhir_resources(doc, original: bytes):
    if doc.media_type != "application/fhir+json" or not original:
        return None, []
    try:
        parsed = FH.load(original)
    except Exception:
        return None, []
    return parsed, FH.resources(parsed)


def classify_text(canonical: str) -> List[str]:
    """Marker candidates from text alone (cheap; for document lists)."""
    return BP.candidates(canonical)


def classify(doc, original: bytes) -> dict:
    _, res = _fhir_resources(doc, original)
    fhir = [{"profile_id": FH.PROFILE_OF[r["resourceType"]], "resource_type": r["resourceType"], "pointer": p}
            for r, p in res]
    cands = [] if fhir else BP.candidates(doc.canonical_text)
    return {"candidates": [{"profile_id": c, "title": BP.PROFILES[c]["title"], "supported": c in PAPER}
                           for c in cands],
            "fhir": fhir, "form_fields_filled": acroform_values(original) if not fhir else 0}


def _not_found(pid: str, rec: dict) -> List[str]:
    names = {f["field"] for f in rec["header"]}
    for t in rec["tables"]:
        for r in t["rows"] + ([t["totals"]] if t.get("totals") else []):
            names |= {f["field"] for f in r["fields"]}
    money_sym = any(f.get("currency_symbol") for t in rec["tables"] for r in t["rows"] for f in r["fields"]) or \
        any(f.get("currency_symbol") for f in rec["header"])
    out = []
    for f in BP.PROFILES[pid]["fields"]:
        if f == "currency":
            if not money_sym:
                out.append(f)
            continue
        if not (_ALIASES.get(f, {f}) & names):
            out.append(f)
    return out


def _meta(pid: str) -> dict:
    p = BP.PROFILES[pid]
    return {"profile_id": pid, "title": p["title"], "family": p["family"], "description": p["description"],
            "standardization": p["standardization"], "profile_notes": list(p["notes"])}


def extract(doc, original: bytes, confirmed: Optional[dict]) -> dict:
    """confirmed = {"profile_id", "by", "at"} for paper documents; ignored for FHIR."""
    key = hashlib.sha256(f"{ADAPTER_VERSION}|{doc.canonical_sha256}|{doc.original_sha256}|"
                         f"{(confirmed or {}).get('profile_id')}".encode()).hexdigest()
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return copy.deepcopy(_cache[key])
    source = {"document_id": doc.doc_id, "document_sha256": doc.original_sha256,
              "canonical_sha256": doc.canonical_sha256}
    out = {"doc_id": doc.doc_id, "engine": ADAPTER_VERSION, "source": source, "records": [], "read_notes": [],
           "issues": []}
    parsed, res = _fhir_resources(doc, original)
    if res:
        for r, p in res:
            rec = FH.extract_resource(r, p, doc.doc_id)
            rec.update(_meta(FH.PROFILE_OF[r["resourceType"]]))
            rec["confirmation"] = {"basis": "STRUCTURAL", "detail": f"resourceType {r['resourceType']} at '{p or '/'}'"}
            out["records"].append(rec)
    elif confirmed and confirmed.get("profile_id") in PAPER:
        pid = confirmed["profile_id"]
        lines, notes = document_lines(doc, original)
        out["read_notes"] = notes
        rec = LO.extract(pid, lines, doc.doc_id)
        rec.update(_meta(pid))
        rec["confirmation"] = {"basis": "PERSON", "by": confirmed.get("by"), "at": confirmed.get("at")}
        rec["not_found"] = _not_found(pid, rec)
        n = acroform_values(original)
        if n:
            rec["issues"].insert(0, {"code": "FORM_FIELDS_NOT_READ",
                                     "message": f"This PDF keeps {n} entries in fillable form fields, which aren't part "
                                                "of its printed text. Print it to PDF (which flattens the fields) and "
                                                "import that copy."})
        out["records"].append(rec)
    with _cache_lock:
        _cache[key] = copy.deepcopy(out)
        while len(_cache) > 64:
            _cache.popitem(last=False)
    return out
